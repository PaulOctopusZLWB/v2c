from __future__ import annotations

import threading

from personal_context_node.config import AppConfig
from personal_context_node.pipeline_adapters import build_pipeline_adapters
from personal_context_node.process_runner import DrainResult, drain_process_queue


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

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def drain_now(self, *, max_steps: int = 200) -> DrainResult:
        """Synchronous drain (used in request handlers and tests)."""
        self._stop.clear()
        adapters = build_pipeline_adapters(config=self._config)
        result = drain_process_queue(
            config=self._config, vad=adapters.vad, asr=adapters.asr, llm=adapters.llm,
            max_steps=max_steps, should_stop=self._stop.is_set, job_name="web.drain",
        )
        self._last_result = result
        return result

    def start(self, *, max_steps: int = 200) -> bool:
        """Start the background drain thread if not already running. Returns started?"""
        with self._lock:
            if self.is_running():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, kwargs={"max_steps": max_steps}, daemon=True)
            self._thread.start()
            return True

    def request_stop(self) -> None:
        self._stop.set()

    def _run(self, *, max_steps: int) -> None:
        adapters = build_pipeline_adapters(config=self._config)
        self._last_result = drain_process_queue(
            config=self._config, vad=adapters.vad, asr=adapters.asr, llm=adapters.llm,
            max_steps=max_steps, should_stop=self._stop.is_set, job_name="web.drain",
        )
