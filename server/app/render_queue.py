from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


MAX_CONCURRENT_RENDERS = 1
RECENT_TTL_SECONDS = 60.0
REAPER_INTERVAL_SECONDS = 5.0


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
        self._inputs: dict[str, dict] = {}
        self._worker: Optional[asyncio.Task] = None
        self._reaper: Optional[asyncio.Task] = None
        self._current_id: Optional[str] = None
        self._current_proc: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._worker_loop())
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reaper_loop())

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
        if self._reaper:
            self._reaper.cancel()
            try:
                await self._reaper
            except asyncio.CancelledError:
                pass
            self._reaper = None

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

    async def _reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(min(REAPER_INTERVAL_SECONDS, RECENT_TTL_SECONDS) / 2)
            cutoff = time.time() - RECENT_TTL_SECONDS
            stale = [
                jid for jid, j in self._jobs.items()
                if j.status in {"done", "failed", "cancelled"}
                and j.finished_at is not None
                and j.finished_at < cutoff
            ]
            for jid in stale:
                self._jobs.pop(jid, None)

    async def _run_job(self, job: JobState) -> None:
        inputs = self._inputs.pop(job.id, None)
        if inputs is None:
            raise RuntimeError("missing inputs for job")
        list_path: Path = inputs["list_path"]
        output_path: Path = inputs["output_path"]
        try:
            if job.format == "mp4":
                await self._run_mp4(job, list_path, output_path)
            elif job.format == "gif":
                await self._run_gif(job, list_path, output_path)
            else:
                raise ValueError(f"unsupported format: {job.format}")
            if not job.cancel_requested:
                job.percent = 100
                job.output_path = str(output_path.relative_to(self._data_dir))
        finally:
            list_path.unlink(missing_ok=True)
            if job.cancel_requested and output_path.exists():
                output_path.unlink(missing_ok=True)

    async def _run_mp4(self, job: JobState, list_path: Path, output_path: Path) -> None:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-vf", f"fps={job.fps},format=yuv420p",
            "-c:v", "libx264", "-movflags", "+faststart",
            "-progress", "pipe:1", str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._current_proc = proc
        if job.cancel_requested:
            proc.terminate()
        last_frame = 0
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            if key == "frame":
                try:
                    last_frame = int(value)
                except ValueError:
                    continue
                job.current_frame = last_frame
                if job.total_frames:
                    job.percent = min(100, round(last_frame / job.total_frames * 100))
                    if job.started_at and last_frame > 0:
                        elapsed = time.time() - job.started_at
                        observed = last_frame / elapsed if elapsed else 0
                        if observed > 0:
                            job.eta_seconds = max(0, int(
                                (job.total_frames - last_frame) / observed
                            ))
            elif key == "progress" and value == "end":
                break
        rc = await proc.wait()
        if rc != 0 and not job.cancel_requested:
            stderr = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
            raise RuntimeError(stderr or "ffmpeg failed")

    async def _run_gif(self, job: JobState, list_path: Path, output_path: Path) -> None:
        palette = output_path.with_suffix(".palette.png")
        try:
            palette_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-vf", f"fps={job.fps},scale=720:-1:flags=lanczos,palettegen",
                str(palette),
            ]
            proc = await asyncio.create_subprocess_exec(
                *palette_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            self._current_proc = proc
            if job.cancel_requested:
                proc.terminate()
            rc = await proc.wait()
            if rc != 0:
                if job.cancel_requested:
                    return
                err = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
                raise RuntimeError(err or "ffmpeg palettegen failed")
            job.percent = 50

            if job.cancel_requested:
                return

            encode_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-i", str(palette),
                "-filter_complex",
                f"fps={job.fps},scale=720:-1:flags=lanczos[x];[x][1:v]paletteuse",
                str(output_path),
            ]
            proc = await asyncio.create_subprocess_exec(
                *encode_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            self._current_proc = proc
            if job.cancel_requested:
                proc.terminate()
            rc = await proc.wait()
            if rc != 0:
                if job.cancel_requested:
                    return
                err = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
                raise RuntimeError(err or "ffmpeg gif encode failed")
        finally:
            palette.unlink(missing_ok=True)

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

    def enqueue_with_inputs(
        self, job: JobState, *, list_path: Path, output_path: Path,
        total_frames: int,
    ) -> int:
        job.total_frames = total_frames
        self._inputs[job.id] = {
            "list_path": list_path,
            "output_path": output_path,
        }
        return self.enqueue(job)

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


_RANGE_PRESETS = {
    "24h": timedelta(days=1),
    "7d":  timedelta(days=7),
}


def resolve_range_preset(
    preset: str, *, now: Optional[datetime] = None
) -> tuple[Optional[str], Optional[str]]:
    if preset == "all":
        return None, None
    if preset not in _RANGE_PRESETS:
        raise ValueError(f"unknown range_preset: {preset}")
    end = (now or datetime.now(timezone.utc))
    start = end - _RANGE_PRESETS[preset]
    return start.isoformat(), end.isoformat()


def unique_video_stem(*, timestamp: str, job_id: str) -> str:
    return f"timelapse-{timestamp}-{job_id[:6]}"
