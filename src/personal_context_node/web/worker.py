from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.ingest import import_audio_files
from personal_context_node.pipeline_adapters import build_pipeline_adapters
from personal_context_node.process_runner import DrainResult, drain_process_queue


logger = logging.getLogger(__name__)


def _close_adapters(adapters) -> None:
    # Release any closeable adapter (the funasr_server PersistentCommandASRAdapter owns a
    # resident model subprocess); mock/command adapters have no close().
    closer = getattr(adapters.asr, "close", None)
    if callable(closer):
        with contextlib.suppress(Exception):
            closer()


class PipelineWorker:
    """Single drain-loop worker. Cooperative stop via an in-process Event.

    Lease-based task claiming (claim_next_task + reclaim_expired_tasks) makes this
    safe to run alongside a launchd worker on the same database.
    """

    def __init__(self, *, config: AppConfig) -> None:
        self._config = config
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_result: DrainResult | None = None
        self._import: dict | None = None
        self._embedding: dict | None = None
        self._embedding_result: dict | None = None
        self._emotion: dict | None = None
        self._emotion_result: dict | None = None
        # DI seam: tests replace this with a factory returning a stub adapter so no real CAM++
        # model is loaded. Default builds a resident PersistentCommandEmbedAdapter from config.
        self._embed_factory = self._default_embed_factory
        # DI seam: tests replace this with a factory returning a stub adapter so no real
        # emotion2vec model is loaded. Default builds a resident PersistentCommandEmotionAdapter.
        self._emotion_factory = self._default_emotion_factory

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def import_state(self) -> dict | None:
        """Return a shallow copy of the in-progress import state (or None)."""
        state = self._import
        return dict(state) if state is not None else None

    def embedding_state(self) -> dict | None:
        """Return a shallow copy of the in-progress embedding-extraction state (or None)."""
        state = self._embedding
        return dict(state) if state is not None else None

    def emotion_state(self) -> dict | None:
        """Return a shallow copy of the in-progress emotion-extraction state (or None)."""
        state = self._emotion
        return dict(state) if state is not None else None

    def _default_embed_factory(self):
        """Build a resident CAM++ embed adapter pointed at the --server wrapper. Imported lazily so
        the heavy adapter module is only touched when a real extraction runs (never in unit tests,
        which inject their own factory)."""
        from personal_context_node.adapters.embed.command import PersistentCommandEmbedAdapter

        return PersistentCommandEmbedAdapter(
            command=[
                "python3", "scripts/funasr_campplus_embed_wrapper.py", "--server",
                "--device", self._config.asr_device,
            ]
        )

    def _default_emotion_factory(self):
        """Build a resident emotion2vec adapter pointed at the --server wrapper. Imported lazily so
        the heavy adapter module is only touched when a real extraction runs (never in unit tests,
        which inject their own factory)."""
        from personal_context_node.adapters.emotion.command import PersistentCommandEmotionAdapter

        return PersistentCommandEmotionAdapter(
            command=[
                "python3", "scripts/funasr_emotion2vec_wrapper.py", "--server",
                "--device", self._config.asr_device,
            ]
        )

    def start_embedding_extraction(
        self, *, session_id: str | None = None, day: str | None = None, embed_factory=None
    ) -> bool:
        """Start a background CAM++ voiceprint extraction over pending segments. Returns started?

        Reuses the single self._thread guard so is_running() is true throughout and start() won't
        double-spawn. ``embed_factory`` (a zero-arg callable returning an object with ``embed`` and
        ``close``) is the DI seam for tests; when omitted it falls back to self._embed_factory.
        """
        with self._lock:
            if self.is_running():
                return False
            self._stop.clear()
            factory = embed_factory or self._embed_factory
            self._embedding = {"active": True, "done": 0, "total": 0}
            self._thread = threading.Thread(
                target=self._extract_embeddings,
                kwargs={"session_id": session_id, "day": day, "factory": factory},
                daemon=True,
            )
            self._thread.start()
            return True

    def start_emotion_extraction(
        self, *, session_id: str | None = None, day: str | None = None, classify_factory=None
    ) -> bool:
        """Start a background acoustic-emotion extraction over pending segments. Returns started?

        Reuses the single self._thread guard so is_running() is true throughout and start() won't
        double-spawn. ``classify_factory`` (a zero-arg callable returning an object with ``classify``
        and ``close``) is the DI seam for tests; when omitted it falls back to self._emotion_factory.
        """
        with self._lock:
            if self.is_running():
                return False
            self._stop.clear()
            factory = classify_factory or self._emotion_factory
            self._emotion = {"active": True, "done": 0, "total": 0}
            self._thread = threading.Thread(
                target=self._extract_emotions,
                kwargs={"session_id": session_id, "day": day, "factory": factory},
                daemon=True,
            )
            self._thread.start()
            return True

    def _drain_to_completion(self, *, max_steps: int = 200) -> DrainResult:
        """Build adapters, loop drain_process_queue in batches of max_steps until the queue is
        empty (status 'complete') or a stop is requested, then close any resident adapter.
        Shared by every drain entry point so the funasr_server subprocess is always released."""
        # Re-read DB-backed runtime overrides each drain so web config changes take effect on the
        # NEXT drain without a restart. ASR overrides go through model_copy; GLM_* overrides are
        # exported to os.environ, which the glm_llm_wrapper subprocess inherits (no env= is passed).
        from personal_context_node import settings as _settings

        overrides = _settings.read_overrides(self._config)
        effective = _settings.effective_config(self._config)
        # apply_glm_env reverts a cleared override to the launch baseline (not the last-applied value).
        _settings.apply_glm_env(overrides)
        adapters = build_pipeline_adapters(config=effective)
        total_steps = 0
        total_succeeded = 0
        total_failed = 0
        last_status = "complete"
        try:
            while not self._stop.is_set():
                result = drain_process_queue(
                    config=self._config, vad=adapters.vad, asr=adapters.asr, llm=adapters.llm,
                    max_steps=max_steps, should_stop=self._stop.is_set, job_name="web.drain",
                )
                total_steps += result.process_steps
                total_succeeded += result.tasks_succeeded
                total_failed += result.tasks_failed
                last_status = result.status
                if result.status in ("complete", "stopped"):
                    break
        finally:
            _close_adapters(adapters)
        return DrainResult(
            process_steps=total_steps,
            tasks_succeeded=total_succeeded,
            tasks_failed=total_failed,
            status=last_status if not self._stop.is_set() else "stopped",
        )

    def drain_now(self, *, max_steps: int = 200) -> DrainResult:
        """Synchronous drain (used in request handlers and tests). Fully drains the queue."""
        self._stop.clear()
        final = self._drain_to_completion(max_steps=max_steps)
        self._last_result = final
        return final

    def start(self, *, max_steps: int = 200) -> bool:
        """Start the background drain thread if not already running. Returns started?"""
        with self._lock:
            if self.is_running():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, kwargs={"max_steps": max_steps}, daemon=True)
            self._thread.start()
            return True

    def start_import(self, source_dir: str) -> bool:
        """Start a background import that streams progress, then drains the queue.

        Reuses the single self._thread guard so is_running() is true throughout and
        start() won't double-spawn. Returns started?
        """
        with self._lock:
            if self.is_running():
                return False
            self._stop.clear()
            self._import = {"active": True, "done": 0, "total": 0, "current": ""}
            self._thread = threading.Thread(
                target=self._import_then_drain, kwargs={"source_dir": source_dir}, daemon=True
            )
            self._thread.start()
            return True

    def request_stop(self) -> None:
        self._stop.set()

    def _run(self, *, max_steps: int) -> None:
        self._last_result = self._drain_to_completion(max_steps=max_steps)

    def _extract_embeddings(self, *, session_id: str | None, day: str | None, factory) -> None:
        from personal_context_node.speaker_embeddings import extract_pending_embeddings

        def _cb(done: int, total: int) -> None:
            self._embedding = {"active": True, "done": done, "total": total}

        adapter = factory()
        try:
            result = extract_pending_embeddings(
                config=self._config, embed_fn=adapter.embed,
                session_id=session_id, day=day, progress=_cb,
            )
            self._embedding_result = result
            self._embedding = {
                "active": False,
                "done": int(result.get("total", 0)),
                "total": int(result.get("total", 0)),
            }
        finally:
            # ALWAYS release the resident model subprocess, even if extraction raised.
            with contextlib.suppress(Exception):
                adapter.close()
            # Guard against an extraction that raised before the success branch set active=False.
            if self._embedding is not None and self._embedding.get("active"):
                state = dict(self._embedding)
                state["active"] = False
                self._embedding = state

    def _extract_emotions(self, *, session_id: str | None, day: str | None, factory) -> None:
        from personal_context_node.segment_emotions import extract_pending_emotions

        def _cb(done: int, total: int) -> None:
            self._emotion = {"active": True, "done": done, "total": total}

        adapter = factory()
        try:
            result = extract_pending_emotions(
                config=self._config, classify_fn=adapter.classify,
                session_id=session_id, day=day, progress=_cb,
            )
            self._emotion_result = result
            self._emotion = {
                "active": False,
                "done": int(result.get("total", 0)),
                "total": int(result.get("total", 0)),
            }
        finally:
            # ALWAYS release the resident model subprocess, even if extraction raised.
            with contextlib.suppress(Exception):
                adapter.close()
            # Guard against an extraction that raised before the success branch set active=False.
            if self._emotion is not None and self._emotion.get("active"):
                state = dict(self._emotion)
                state["active"] = False
                self._emotion = state

    def _import_then_drain(self, *, source_dir: str) -> None:
        from personal_context_node import settings as _settings

        def _cb(done: int, total: int, name: str) -> None:
            self._import = {"active": True, "done": done, "total": total, "current": name}

        try:
            try:
                # Use the effective config so a web asr_mode=diarize override routes new imports to
                # transcribe_diarize (ingest picks the task_type from config.asr_mode).
                import_audio_files(
                    config=_settings.effective_config(self._config), source_dir=Path(source_dir), progress=_cb,
                )
            except Exception:
                logger.exception("import failed; continuing to drain any already queued tasks")
            finally:
                # Mark import phase inactive (even on error) before draining; the SSE
                # bar reads active=False as "import done, now transcribing".
                final = self._import or {"done": 0, "total": 0}
                total = int(final.get("total", 0))
                done = int(final.get("done", total))
                self._import = {"active": False, "done": done, "total": total, "current": ""}
            self._last_result = self._drain_to_completion()
        finally:
            # Guard against an import that raised before the inner finally set active=False.
            if self._import is not None and self._import.get("active"):
                state = dict(self._import)
                state["active"] = False
                state["current"] = ""
                self._import = state
