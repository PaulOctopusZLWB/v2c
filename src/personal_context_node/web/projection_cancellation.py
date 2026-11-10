"""Cancel a user-requested UMAP fit without interrupting the pipeline or database."""
from __future__ import annotations

import multiprocessing
import atexit
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProjectionJob:
    cancelled: threading.Event = field(default_factory=threading.Event)
    process: Any = None


_LOCK = threading.Lock()
_JOBS: dict[str, ProjectionJob] = {}
_CANCELLED: dict[str, float] = {}
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_WORKER_GATE = threading.Lock()
_WORKER: Any = None
_WORKER_PIPE: Any = None
_WARM_ENABLED = False


def register(job_id: str) -> ProjectionJob:
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("投射请求标识无效")
    with _LOCK:
        now = time.monotonic()
        for key, timestamp in list(_CANCELLED.items()):
            if now - timestamp > 60:
                _CANCELLED.pop(key, None)
        if job_id in _CANCELLED:
            _CANCELLED.pop(job_id, None)
            raise ValueError("投射已取消")
        if job_id in _JOBS:
            raise ValueError("投射请求已存在")
        if sum(not existing.cancelled.is_set() for existing in _JOBS.values()) >= 2:
            raise ValueError("已有投射任务正在运行，请稍后重试")
        job = ProjectionJob()
        _JOBS[job_id] = job
        return job


def cancel(job_id: str) -> None:
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("投射请求标识无效")
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            # The cancel request may arrive before the projection request is registered.
            _CANCELLED[job_id] = time.monotonic()
            return
        job.cancelled.set()
        if job.process is not None and job.process.is_alive():
            job.process.terminate()


def finish(job_id: str, job: ProjectionJob) -> None:
    with _LOCK:
        if _JOBS.get(job_id) is job:
            _JOBS.pop(job_id, None)


def _fit_worker(pipe: Any, warm: bool) -> None:
    """Keep a prewarmed, cancellable process; it only receives vectors, never a DB handle."""
    from personal_context_node import speaker_embeddings as embeddings

    try:
        if warm:
            embeddings.warm_projection_engine().join()
        while True:
            try:
                message = pipe.recv()
            except (EOFError, OSError):
                break
            if message is None:
                break
            matrix, kwargs = message
            try:
                coords, method = embeddings._fit_coords_2d(matrix, **kwargs)
                pipe.send(("ok", coords, method))
            except Exception as exc:
                pipe.send(("error", str(exc)))
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        pipe.close()


def _discard_worker() -> None:
    """Called only while holding _WORKER_GATE."""
    global _WORKER, _WORKER_PIPE
    process, pipe = _WORKER, _WORKER_PIPE
    _WORKER = _WORKER_PIPE = None
    if process is not None:
        if process.is_alive():
            try:
                if pipe is not None:
                    pipe.send(None)
                process.join(timeout=2)
            except (BrokenPipeError, EOFError, OSError):
                pass
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
    if pipe is not None:
        pipe.close()


def _ensure_worker(*, warm: bool = False):
    """Called only while holding _WORKER_GATE."""
    global _WORKER, _WORKER_PIPE
    if _WORKER is not None and _WORKER.is_alive():
        return _WORKER, _WORKER_PIPE
    _discard_worker()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_fit_worker, args=(child, warm), daemon=True)
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        raise
    child.close()
    _WORKER, _WORKER_PIPE = process, parent
    return process, parent


def warm_projection_worker() -> threading.Thread:
    """Start warming in the worker at server boot, without delaying the web server."""
    global _WARM_ENABLED
    _WARM_ENABLED = True
    def start() -> None:
        with _WORKER_GATE:
            if _WARM_ENABLED:
                _ensure_worker(warm=True)

    thread = threading.Thread(target=start, name="projection-worker-warmup", daemon=True)
    thread.start()
    return thread


def stop_projection_worker() -> None:
    global _WARM_ENABLED
    _WARM_ENABLED = False
    if _WORKER_GATE.acquire(timeout=1):
        try:
            _discard_worker()
        finally:
            _WORKER_GATE.release()


atexit.register(stop_projection_worker)


def fit(job: ProjectionJob, matrix: Any, **kwargs: Any):
    from personal_context_node import speaker_embeddings as embeddings

    if job.cancelled.is_set():
        raise ValueError("投射已取消")
    cache_key = embeddings._coords_cache_key(matrix, **kwargs)
    with embeddings._COORDS_LOCK:
        cached = embeddings._COORDS_CACHE.get(cache_key)
    if cached is not None:
        if job.cancelled.is_set():
            raise ValueError("投射已取消")
        return cached
    while not _WORKER_GATE.acquire(timeout=0.1):
        if job.cancelled.is_set():
            raise ValueError("投射已取消")
    replace = False
    try:
        if job.cancelled.is_set():
            raise ValueError("投射已取消")
        process, pipe = _ensure_worker()
        with _LOCK:
            if job.cancelled.is_set():
                raise ValueError("投射已取消")
            job.process = process
        pipe.send((matrix, kwargs))
        message = None
        while not job.cancelled.is_set():
            if pipe.poll(0.1):
                try:
                    message = pipe.recv()
                except EOFError:
                    break
                break
            if not process.is_alive():
                break
        if job.cancelled.is_set():
            replace = True
            raise ValueError("投射已取消")
        if not message or message[0] != "ok":
            replace = not process.is_alive() or not message
            raise ValueError("UMAP 投射失败，请稍后重试")
        _, coords, method = message
        if method == kwargs["method"]:
            with embeddings._COORDS_LOCK:
                embeddings._COORDS_CACHE[cache_key] = (coords, method)
                while len(embeddings._COORDS_CACHE) > embeddings._COORDS_CACHE_MAX:
                    embeddings._COORDS_CACHE.pop(next(iter(embeddings._COORDS_CACHE)))
        return coords, method
    except (BrokenPipeError, EOFError, OSError) as exc:
        replace = True
        if job.cancelled.is_set():
            raise ValueError("投射已取消") from exc
        raise ValueError("UMAP 投射失败，请稍后重试") from exc
    finally:
        with _LOCK:
            job.process = None
        if replace:
            _discard_worker()
        _WORKER_GATE.release()
        if replace and _WARM_ENABLED:
            warm_projection_worker()
