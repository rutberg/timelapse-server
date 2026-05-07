from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


MAX_CONCURRENT_RENDERS = 1
RECENT_TTL_SECONDS = 60.0


@dataclass
class JobState:
    id: str
    camera_id: str
    format: str
    fps: int
    start_at: Optional[str]
    end_at: Optional[str]
    range_preset: Optional[str]
    name: str
    queued_at: float
    status: str = "queued"
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total_frames: Optional[int] = None
    current_frame: Optional[int] = None
    percent: Optional[int] = None
    eta_seconds: Optional[int] = None
    error: Optional[str] = None
    output_path: Optional[str] = None
    cancel_requested: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class RenderRunner:
    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._jobs: dict[str, JobState] = {}
        self._order: list[str] = []
        self._worker: Optional[asyncio.Task] = None
        self._reaper: Optional[asyncio.Task] = None
        self._current_id: Optional[str] = None
        self._current_proc: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        if self._current_proc and self._current_proc.returncode is None:
            self._current_proc.terminate()
            try:
                await asyncio.wait_for(self._current_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._current_proc.kill()
                await self._current_proc.wait()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                self._order.remove(job_id)
            except ValueError:
                pass
            job = self._jobs[job_id]
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = time.time()
                self._queue.task_done()
                continue
            job.status = "running"
            job.started_at = time.time()
            self._current_id = job_id
            try:
                await self._run_job(job)
                job.status = "cancelled" if job.cancel_requested else "done"
            except asyncio.CancelledError:
                job.status = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.error = str(exc)
            finally:
                job.finished_at = time.time()
                self._current_id = None
                self._current_proc = None
                self._queue.task_done()

    async def _run_job(self, job: JobState) -> None:
        raise NotImplementedError

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status in {"done", "failed", "cancelled"}:
            return True  # idempotent
        job.cancel_requested = True
        if self._current_id == job_id and self._current_proc is not None:
            if self._current_proc.returncode is None:
                self._current_proc.terminate()
        return True

    def enqueue(self, job: JobState) -> int:
        if job.id in self._jobs:
            raise ValueError(f"duplicate job id: {job.id}")
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._queue.put_nowait(job.id)
        return len(self._order)

    def snapshot(self) -> dict:
        running = self._jobs[self._current_id].to_dict() if self._current_id else None
        queued = [self._jobs[jid].to_dict() for jid in self._order]
        recent = [
            j.to_dict() for j in self._jobs.values()
            if j.status in {"done", "failed", "cancelled"}
            and j.finished_at is not None
            and (time.time() - j.finished_at) < RECENT_TTL_SECONDS
        ]
        recent.sort(key=lambda d: d["finished_at"] or 0, reverse=True)
        return {"running": running, "queued": queued, "recent": recent}
