"""Cancel a user-requested UMAP fit without interrupting the pipeline or database."""
from __future__ import annotations

import multiprocessing
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
        if len(_JOBS) >= 2:
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


def _fit_worker(pipe: Any, matrix: Any, kwargs: dict[str, Any], cache: dict) -> None:
    """The subprocess receives only vectors and parameters; it never opens the database."""
    try:
        from personal_context_node import speaker_embeddings as embeddings

        embeddings._COORDS_CACHE.update(cache)
        coords, method = embeddings._fit_coords_2d(matrix, **kwargs)
        pipe.send(("ok", coords, method, embeddings._COORDS_CACHE))
    except BaseException as exc:
        try:
            pipe.send(("error", str(exc)))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        pipe.close()


def fit(job: ProjectionJob, matrix: Any, **kwargs: Any):
    from personal_context_node import speaker_embeddings as embeddings

    if job.cancelled.is_set():
        raise ValueError("投射已取消")
    with embeddings._COORDS_LOCK:
        cache = dict(embeddings._COORDS_CACHE)
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_fit_worker, args=(send, matrix, kwargs, cache), daemon=True)
    try:
        with _LOCK:
            if job.cancelled.is_set():
                raise ValueError("投射已取消")
            job.process = process
            process.start()
        send.close()
        message = None
        while not job.cancelled.is_set():
            if receive.poll(0.1):
                try:
                    message = receive.recv()
                except EOFError:
                    break
                break
            if not process.is_alive():
                break
        if job.cancelled.is_set():
            raise ValueError("投射已取消")
        if not message or message[0] != "ok":
            raise ValueError("UMAP 投射失败，请稍后重试")
        _, coords, method, child_cache = message
        with embeddings._COORDS_LOCK:
            for key, value in child_cache.items():
                embeddings._COORDS_CACHE[key] = value
            while len(embeddings._COORDS_CACHE) > embeddings._COORDS_CACHE_MAX:
                embeddings._COORDS_CACHE.pop(next(iter(embeddings._COORDS_CACHE)))
        return coords, method
    finally:
        receive.close()
        send.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        with _LOCK:
            job.process = None
