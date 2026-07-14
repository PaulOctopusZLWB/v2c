from __future__ import annotations

import contextlib
import logging
import threading
from dataclasses import asdict
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.ingest import IngestProgressUpdate, import_audio_files
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
        # Resident pipeline adapters, cached across drains so the funasr_server model
        # subprocess survives between runs (reloading the model per drain costs seconds
        # to tens of seconds). Keyed by the effective config (+ adapter factory identity,
        # so a test-monkeypatched build_pipeline_adapters is never served a stale cache);
        # a web settings change rebuilds on the next drain, same as before.
        self._adapters = None
        self._adapters_key: str | None = None
        self._import: dict | None = None
        self._embedding: dict | None = None
        self._embedding_result: dict | None = None
        self._emotion: dict | None = None
        self._emotion_result: dict | None = None
        # Combined embedding+emotion extraction result (start_combined_extraction). Progress is
        # reported through the EXISTING self._embedding / self._emotion slots (see
        # _extract_embeddings_and_emotions) so the current SSE/status routes -- which read
        # embedding_state()/emotion_state() and know nothing about a combined run -- keep working
        # unchanged; this slot only carries the combined run's final result for callers that want
        # it (e.g. a future combined-specific route), without adding a new route dependency.
        self._combined_result: dict | None = None
        # DI seam: tests replace this with a factory returning a stub adapter so no real CAM++
        # model is loaded. Default builds a resident PersistentCommandEmbedAdapter from config.
        self._embed_factory = self._default_embed_factory
        # DI seam: tests replace this with a factory returning a stub adapter so no real
        # emotion2vec model is loaded. Default builds a resident PersistentCommandEmotionAdapter.
        self._emotion_factory = self._default_emotion_factory
        # Resident (embed, emotion) adapter pair for the pipeline's extract_features task,
        # cached across drains like self._adapters so the CAM++/emotion2vec subprocesses
        # survive between runs. Built from the SAME factories the manual extraction routes use.
        self._feature_adapters: tuple[object, object] | None = None
        self._feature_adapters_key: str | None = None

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

        command = [
            "python3", "scripts/funasr_campplus_embed_wrapper.py", "--server",
            "--device", self._config.asr_device,
        ]
        if self._config.asr_precision == "fp16":
            command.extend(["--precision", "fp16"])
        return PersistentCommandEmbedAdapter(command=command)

    def _default_emotion_factory(self):
        """Build a resident emotion2vec adapter pointed at the --server wrapper. Imported lazily so
        the heavy adapter module is only touched when a real extraction runs (never in unit tests,
        which inject their own factory)."""
        from personal_context_node.adapters.emotion.command import PersistentCommandEmotionAdapter

        command = [
            "python3", "scripts/funasr_emotion2vec_wrapper.py", "--server",
            "--device", self._config.asr_device,
        ]
        if self._config.asr_precision == "fp16":
            command.extend(["--precision", "fp16"])
        return PersistentCommandEmotionAdapter(command=command)

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

    def start_combined_extraction(
        self,
        *,
        session_id: str | None = None,
        day: str | None = None,
        embed_factory=None,
        classify_factory=None,
    ) -> bool:
        """Start a background CAM++ embedding + emotion2vec classification pass in ONE sweep.

        Uses extract_pending_embeddings_and_emotions so each pending segment's audio path is
        resolved once instead of twice (once per standalone extraction). Reuses the single
        self._thread guard so is_running() is true throughout and start() won't double-spawn
        against an embedding-only or emotion-only run either. ``embed_factory``/``classify_factory``
        (zero-arg callables returning an object with ``embed``/``close`` and ``classify``/``close``
        respectively) are the DI seam for tests; when omitted they fall back to
        self._embed_factory / self._emotion_factory. BOTH resident model subprocesses are released
        in `finally`, even if one factory or the extraction itself raises.
        """
        with self._lock:
            if self.is_running():
                return False
            self._stop.clear()
            embed_fac = embed_factory or self._embed_factory
            classify_fac = classify_factory or self._emotion_factory
            self._embedding = {"active": True, "done": 0, "total": 0}
            self._emotion = {"active": True, "done": 0, "total": 0}
            self._thread = threading.Thread(
                target=self._extract_embeddings_and_emotions,
                kwargs={
                    "session_id": session_id, "day": day,
                    "embed_factory": embed_fac, "classify_factory": classify_fac,
                },
                daemon=True,
            )
            self._thread.start()
            return True

    def _resident_adapters(self, effective):
        """Return cached pipeline adapters, rebuilding only when the effective config (or the
        adapter factory, under test monkeypatching) changed since the last drain. Keeping the
        adapters resident keeps the funasr_server model loaded across drains."""
        key = f"{id(build_pipeline_adapters)}:{effective.model_dump_json()}"
        if self._adapters is None or key != self._adapters_key:
            self.close_adapters()
            self._adapters = build_pipeline_adapters(config=effective)
            self._adapters_key = key
        return self._adapters

    def close_adapters(self) -> None:
        """Release any resident adapter subprocess (config change, app shutdown)."""
        if self._adapters is not None:
            _close_adapters(self._adapters)
            self._adapters = None
            self._adapters_key = None
        self.close_feature_adapters()

    def _resident_feature_adapters(self, effective) -> tuple[object, object]:
        """Return the cached (embed, emotion) adapter pair for extract_features tasks,
        rebuilding only when the effective config (or a test-monkeypatched factory) changed."""
        key = f"{id(self._embed_factory)}:{id(self._emotion_factory)}:{effective.model_dump_json()}"
        if self._feature_adapters is None or key != self._feature_adapters_key:
            self.close_feature_adapters()
            self._feature_adapters = (self._embed_factory(), self._emotion_factory())
            self._feature_adapters_key = key
        return self._feature_adapters

    def close_feature_adapters(self) -> None:
        """Release the resident extract_features adapter pair (config change, app shutdown)."""
        if self._feature_adapters is not None:
            for adapter in self._feature_adapters:
                closer = getattr(adapter, "close", None)
                if callable(closer):
                    with contextlib.suppress(Exception):
                        closer()
            self._feature_adapters = None
            self._feature_adapters_key = None

    def _drain_to_completion(self, *, max_steps: int = 200) -> DrainResult:
        """Loop drain_process_queue in batches of max_steps until the queue is empty (status
        'complete') or a stop is requested. Adapters stay resident across drains (see
        _resident_adapters); close_adapters() releases them on config change or shutdown."""
        # Re-read DB-backed runtime overrides each drain so web config changes take effect on the
        # NEXT drain without a restart. ASR overrides go through model_copy; GLM_* overrides are
        # exported to os.environ, which the glm_llm_wrapper subprocess inherits (no env= is passed).
        from personal_context_node import settings as _settings

        overrides = _settings.read_overrides(self._config)
        effective = _settings.effective_config(self._config)
        # apply_glm_env reverts a cleared override to the launch baseline (not the last-applied value).
        _settings.apply_glm_env(overrides)
        adapters = self._resident_adapters(effective)
        workers = max(1, int(getattr(effective, "pipeline_workers", 1) or 1))
        total_steps = 0
        total_succeeded = 0
        total_failed = 0
        last_status = "complete"
        # The adapter objects are lazy (subprocess spawns on first use), so passing the resident
        # pair into every drain costs nothing until an extract_features task actually runs.
        feature_embed, feature_emotion = self._resident_feature_adapters(effective)
        while not self._stop.is_set():
            result = drain_process_queue(
                config=self._config, vad=adapters.vad, asr=adapters.asr, llm=adapters.llm,
                embed=feature_embed, emotion=feature_emotion,
                max_steps=max_steps, should_stop=self._stop.is_set, job_name="web.drain",
                workers=workers,
            )
            total_steps += result.process_steps
            total_succeeded += result.tasks_succeeded
            total_failed += result.tasks_failed
            last_status = result.status
            if result.status in ("complete", "stopped"):
                break
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
            self._import = {
                "active": True,
                "phase": "scanning",
                "scanned_files": 0,
                "duplicate_files": 0,
                "new_files": 0,
                "imported_files": 0,
                "done": 0,
                "total": 0,
                "current": "",
                "bytes_done": 0,
                "bytes_total": 0,
                "eta_seconds": None,
            }
            self._thread = threading.Thread(
                target=self._import_then_drain, kwargs={"source_dir": source_dir}, daemon=True
            )
            self._thread.start()
            return True

    def request_stop(self) -> None:
        self._stop.set()

    def _run(self, *, max_steps: int) -> None:
        self._last_result = self._drain_to_completion(max_steps=max_steps)

    def _identify_after_extraction(self, *, session_id: str | None, day: str | None) -> None:
        """Run the automatic identify pass for every session the extraction touched.

        UI-triggered extractions bypass the pipeline's extract_features→identify_speakers edge,
        so close the same loop here: "提取声纹" always ends in identified sessions regardless of
        which entry point ran it. Best-effort per session — an identify failure must never mark
        the extraction itself as failed (mirrors the leaf's gates-nothing philosophy).
        """
        from personal_context_node.speaker_identify import identify_session_speakers
        from personal_context_node.storage.sqlite import connect, fetch_all, initialize

        conn = connect(self._config.database_path)
        try:
            initialize(conn)
            if session_id is not None:
                session_ids = [session_id]
            else:
                where = "where s.date_key = ?" if day is not None else ""
                rows = fetch_all(
                    conn,
                    f"""
                    select distinct s.session_id
                    from sessions s
                    join transcript_segments ts on ts.session_id = s.session_id
                    join segment_embeddings se on se.segment_id = ts.segment_id
                    {where}
                    order by s.started_at
                    """,
                    (day,) if day is not None else (),
                )
                session_ids = [str(row["session_id"]) for row in rows]
        finally:
            conn.close()
        for sid in session_ids:
            with contextlib.suppress(Exception):
                identify_session_speakers(config=self._config, session_id=sid)

    def _extract_embeddings(self, *, session_id: str | None, day: str | None, factory) -> None:
        from personal_context_node.speaker_embeddings import extract_pending_embeddings

        def _cb(done: int, total: int) -> None:
            self._embedding = {"active": True, "done": done, "total": total}

        adapter = factory()
        succeeded = False
        try:
            result = extract_pending_embeddings(
                config=self._config, embed_fn=adapter.embed,
                # getattr: a test-injected stub without embed_batch falls back to the serial path.
                embed_batch_fn=getattr(adapter, "embed_batch", None),
                batch_size=max(1, int(getattr(self._config, "extraction_batch_size", 32) or 32)),
                session_id=session_id, day=day, progress=_cb,
            )
            self._embedding_result = result
            self._embedding = {
                "active": False,
                "done": int(result.get("total", 0)),
                "total": int(result.get("total", 0)),
            }
            succeeded = True
        finally:
            # ALWAYS release the resident model subprocess, even if extraction raised.
            with contextlib.suppress(Exception):
                adapter.close()
            # Guard against an extraction that raised before the success branch set active=False.
            if self._embedding is not None and self._embedding.get("active"):
                state = dict(self._embedding)
                state["active"] = False
                self._embedding = state
        if succeeded:
            # After the adapter is released: identify is CPU/sqlite work and must not extend the
            # resident MPS subprocess's lifetime.
            self._identify_after_extraction(session_id=session_id, day=day)

    def _extract_emotions(self, *, session_id: str | None, day: str | None, factory) -> None:
        from personal_context_node.segment_emotions import extract_pending_emotions

        def _cb(done: int, total: int) -> None:
            self._emotion = {"active": True, "done": done, "total": total}

        adapter = factory()
        try:
            result = extract_pending_emotions(
                config=self._config, classify_fn=adapter.classify,
                # getattr: a test-injected stub without classify_batch falls back to the serial path.
                classify_batch_fn=getattr(adapter, "classify_batch", None),
                batch_size=max(1, int(getattr(self._config, "extraction_batch_size", 32) or 32)),
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

    def _extract_embeddings_and_emotions(
        self, *, session_id: str | None, day: str | None, embed_factory, classify_factory
    ) -> None:
        from personal_context_node.speaker_embeddings import extract_pending_embeddings_and_emotions

        def _cb(done: int, total: int) -> None:
            # One shared progress stream (see extract_pending_embeddings_and_emotions: "done" ticks
            # once per segment regardless of how many artifacts it needed) fans out to BOTH existing
            # state slots so embedding_state()/emotion_state() (and any SSE route reading them) show
            # the same live progress during a combined run, with no route changes required.
            self._embedding = {"active": True, "done": done, "total": total}
            self._emotion = {"active": True, "done": done, "total": total}

        embed_adapter = None
        classify_adapter = None
        succeeded = False
        try:
            embed_adapter = embed_factory()
            classify_adapter = classify_factory()
            result = extract_pending_embeddings_and_emotions(
                config=self._config,
                embed_fn=embed_adapter.embed,
                classify_fn=classify_adapter.classify,
                # getattr: stubs without the batch methods fall back to the serial union pass.
                embed_batch_fn=getattr(embed_adapter, "embed_batch", None),
                classify_batch_fn=getattr(classify_adapter, "classify_batch", None),
                batch_size=max(1, int(getattr(self._config, "extraction_batch_size", 32) or 32)),
                session_id=session_id, day=day, progress=_cb,
            )
            self._combined_result = result
            embedding_result = result.get("embedding", {})
            emotion_result = result.get("emotion", {})
            self._embedding_result = embedding_result
            self._emotion_result = emotion_result
            self._embedding = {
                "active": False,
                "done": int(embedding_result.get("total", 0)),
                "total": int(embedding_result.get("total", 0)),
            }
            self._emotion = {
                "active": False,
                "done": int(emotion_result.get("total", 0)),
                "total": int(emotion_result.get("total", 0)),
            }
            succeeded = True
        finally:
            # ALWAYS release BOTH resident model subprocesses, even if extraction (or building
            # either factory) raised -- two models are resident at once here, so neither must leak.
            if classify_adapter is not None:
                with contextlib.suppress(Exception):
                    classify_adapter.close()
            if embed_adapter is not None:
                with contextlib.suppress(Exception):
                    embed_adapter.close()
            # Guard against a raise before the success branch set active=False on either slot.
            if self._embedding is not None and self._embedding.get("active"):
                state = dict(self._embedding)
                state["active"] = False
                self._embedding = state
            if self._emotion is not None and self._emotion.get("active"):
                state = dict(self._emotion)
                state["active"] = False
                self._emotion = state
        if succeeded:
            # After BOTH adapters are released (identify must not extend MPS residency).
            self._identify_after_extraction(session_id=session_id, day=day)

    def _import_then_drain(self, *, source_dir: str) -> None:
        from personal_context_node import settings as _settings

        def _cb(update: IngestProgressUpdate) -> None:
            self._import = {"active": True, **asdict(update)}

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
                final = dict(self._import or {"done": 0, "total": 0})
                final["active"] = False
                final["phase"] = "complete"
                final["current"] = ""
                final["eta_seconds"] = None
                self._import = final
            self._last_result = self._drain_to_completion()
        finally:
            # Guard against an import that raised before the inner finally set active=False.
            if self._import is not None and self._import.get("active"):
                state = dict(self._import)
                state["active"] = False
                state["current"] = ""
                self._import = state
