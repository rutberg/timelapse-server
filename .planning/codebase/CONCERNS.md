# Codebase Concerns

**Analysis Date:** 2026-05-08

---

## Tech Debt

**Deprecated FastAPI lifecycle hooks:**
- Issue: `@app.on_event("startup")` and `@app.on_event("shutdown")` are deprecated since FastAPI 0.93 in favour of the `lifespan` context manager.
- Files: `server/app/main.py:52`, `server/app/main.py:59`
- Impact: Will emit deprecation warnings and will need migration before FastAPI removes the API.
- Fix approach: Replace both handlers with a single `@asynccontextmanager` lifespan function passed to `FastAPI(lifespan=...)`.

**Duplicated gphoto2 parsing logic:**
- Issue: `parse_list_all_config` and `parse_body_info` / vendor-prefix tables are duplicated verbatim between `agent/timelapse_agent.py:1015-1115` and `server/app/dslr_discovery.py:217-284`. The agent is a single standalone file (no imports from server), which justifies the duplication architecturally, but the two implementations can drift.
- Files: `agent/timelapse_agent.py:1015`, `server/app/dslr_discovery.py:217`
- Impact: A bug fix or vendor addition in one copy silently leaves the other stale.
- Fix approach: Accept the duplication as a deliberate standalone-agent constraint but add a comment cross-referencing both files; consider a property-based test that runs both parsers on the same fixture and asserts identical output.

**`AgentStore._write` accessed via private attribute:**
- Issue: `store._write(agent)` is called directly from `provision_agent` with a `# type: ignore[attr-defined]` suppression to update the ip_fallback mid-provision.
- Files: `server/app/main.py:1034`
- Impact: Breaks encapsulation; any refactor of `AgentStore._write` must remember this callsite.
- Fix approach: Add an `AgentStore.update_ip_fallback(agent_id, ip)` method or fold the ip update into `update_status`.

**Legacy key map used when no property map is present:**
- Issue: `_DSLR_INIT_KEY_MAP` and `_DSLR_SEQUENCE_KEY_MAP` in `agent/timelapse_agent.py:760-773` are Canon-biased fallbacks. Cameras without a property map (i.e., never discovered) silently use Canon gphoto2 key names. Nikon and Sony bodies will get silent `--set-config` failures.
- Files: `agent/timelapse_agent.py:760`, `agent/timelapse_agent.py:893-943`
- Impact: Users who skip DSLR discovery will see settings not applied without any error surfaced to the UI.
- Fix approach: Log a one-time warning when falling back to the legacy map; document that discovery is required for non-Canon bodies.

---

## Known Bugs

**`/api/cameras` blocks the event loop on large deployments:**
- Symptoms: Every call to `GET /api/cameras` triggers `camera_summary` for every registered camera, which calls `list_camera_images` (a full `sorted(base.glob("*/*.jpg"))`) plus `compute_stats` (a three-directory `rglob("*")` walk). With N cameras × M images this is O(N×M) synchronous I/O in FastAPI's thread pool on every dashboard refresh.
- Files: `server/app/main.py:1079-1086`, `server/app/main.py:894-906`, `server/app/main.py:545-557`, `server/app/main.py:532-542`
- Trigger: Any dashboard poll with multiple cameras accumulating thousands of frames.
- Workaround: None; the 15-second frontend poll interval (`camera.js:2511`) mitigates frequency but does not bound latency.

**Queue position returned by `enqueue` drifts after cancellation:**
- Symptoms: `enqueue` returns `len(self._order)` as the queue position. After a queued job is eagerly cancelled (removed from `_order`), new enqueues return a lower position number that may match a previously reported number.
- Files: `server/app/render_queue.py:294-300`
- Trigger: Cancelling a queued render job then immediately requesting a new render.
- Workaround: Position is cosmetic in the UI; no functional breakage, but the displayed position can jump non-monotonically.

**`upload_image` holds image bytes in memory before writing:**
- Symptoms: FastAPI's `UploadFile` buffers the body in a `SpooledTemporaryFile`; `shutil.copyfileobj(image.file, output_file)` reads the entire JPEG before writing. For a 20 MP DSLR RAW-converted JPEG (~15 MB) this doubles peak memory use compared to a streaming write.
- Files: `server/app/main.py:1411-1413`
- Trigger: High-resolution DSLR image uploads.
- Workaround: Acceptable at current scale; becomes problematic if multiple agents upload large images concurrently.

---

## Security Considerations

**No authentication beyond LAN IP allowlist:**
- Risk: Any device on the LAN can call all API endpoints — read frames, delete cameras, trigger provisioning, or upload arbitrary image data. The allowlist (`TIMELAPSE_ALLOWED_NETWORKS`) provides network-layer access control but no per-user or per-agent credential check.
- Files: `server/app/main.py:502-510`, `server/app/main.py:929-932`
- Current mitigation: Default allowlist covers RFC-1918 ranges + loopback only; `TIMELAPSE_PUBLIC_URL` override allows intentional external access.
- Recommendations: For deployments accessible beyond a trusted home LAN, add a shared bearer token or per-agent API key header check. At minimum, the upload and delete endpoints deserve a separate token.

**`sudo_password` flows through shell heredoc:**
- Risk: The sudo password is single-quoted and injected into a bash heredoc via `_shell_single_quote`. Single-quoting is correct for literal shell values, but the password value is later echoed to `sudo -S` stdin. If a future code path logs `install_script`, the password appears in logs.
- Files: `server/app/provision_script.py:45-63`
- Current mitigation: `_shell_single_quote` prevents metacharacter injection; the script is never logged at INFO/DEBUG level in the current code.
- Recommendations: Ensure the generated `install_script` string is never written to disk or logged. Add a note in `build_install_script` that the caller must not log the return value.

**`agent_to_response` reads SSH public key on every GET /api/agents/{id}:**
- Risk: Minor — public keys are not secret, but the function silently returns no key if the path doesn't exist rather than erroring. There's no check that the key is well-formed.
- Files: `server/app/main.py:958-976`
- Current mitigation: Public keys are only returned when `include_public_key=True`.
- Recommendations: No immediate action needed.

---

## Performance Bottlenecks

**`GET /api/cameras` — O(N×M) filesystem walk on every poll:**
- Problem: `camera_summary` calls `list_camera_images` (full glob of all JPEG files) for every camera. `compute_stats` does three recursive `rglob("*")` directory walks. Both run synchronously in the thread pool, blocking other requests.
- Files: `server/app/main.py:1079-1086`, `server/app/main.py:532-557`
- Cause: Image count and storage stats are computed live from the filesystem on every request; there is no in-memory cache or stored counter.
- Improvement path: Cache `image_count` and `storage_bytes` in `config.json` (updated on upload and delete), or compute them lazily with a short TTL. Even a 30-second memoization would eliminate 98% of the I/O.

**`GET /api/cameras/{id}/frame-days` loads all frame paths into memory:**
- Problem: `list_valid_camera_frames` returns every JPEG path for a camera as a Python list before any pagination. A camera with 2 years of daily captures at 96/day ≈ 70 000 entries. The endpoint then iterates the list twice more.
- Files: `server/app/main.py:1438-1468`, `server/app/main.py:691-697`
- Cause: No streaming or database-backed index; full filesystem enumeration.
- Improvement path: Build a lightweight SQLite index of (camera_id, day, filename, size) updated on upload/delete; serve frame-days from the index.

**Thumbnail generation on the request path:**
- Problem: `GET /api/cameras/{id}/frames/{day}/{filename}/thumbnail` generates the thumbnail synchronously in the request handler (via `ensure_frame_thumbnail` which calls `subprocess.run(..., timeout=30)`). This blocks the response for up to 30 seconds.
- Files: `server/app/main.py:1526-1542`, `server/app/main.py:765-800`
- Cause: `ensure_frame_thumbnail` is called inline rather than deferred. The upload handler does queue it as a `BackgroundTask`, so most thumbnails will be pre-generated — but if the background task hasn't run yet (e.g. server restart), the first thumbnail request blocks.
- Improvement path: Accept this as a rare case; add a short timeout or return 202 with a placeholder.

---

## Fragile Areas

**`config.json` as the sole data store — no write concurrency protection:**
- Files: `server/app/main.py:581-631`, `server/app/main.py:1144-1198`
- Why fragile: Every mutating endpoint does a read-load-modify-save cycle on `config.json`. Only the `set_camera_featured` endpoint is protected by `_feature_lock` (`main.py:1112`). All other endpoints (checkin, config update, DSLR discovery, delete) can race if two agents check in at the same moment or if the user triggers multiple UI operations concurrently. The atomic `tempfile → replace` write prevents corruption, but the last writer wins and intermediate updates can be lost.
- Safe modification: Wrap `load_store` / `save_store` pairs in a module-level `threading.Lock` (sync endpoints run in a thread pool), or migrate state to SQLite. The `_feature_lock` pattern is the right model; extend it globally.
- Test coverage: No concurrent-write tests exist.

**`run_provision` is a synchronous blocking call (up to 600 s) in FastAPI's thread pool:**
- Files: `server/app/main.py:1021-1075`, `server/app/ssh_provision.py:81-134`
- Why fragile: FastAPI's default thread pool has a bounded size. A long SSH provision (apt-get install on a slow SD card) can hold a thread for the full 600-second timeout, starving other sync endpoints. Currently harmless with a single-node home deployment, but breaks under any concurrent access.
- Safe modification: Move provisioning to a background asyncio task or a dedicated worker; return a `202 Accepted` with a job ID and let the client poll.
- Test coverage: `tests/server/test_provision_endpoint.py` mocks `run_provision` and never tests concurrency.

**`RenderRunner` state is purely in-memory — lost on restart:**
- Files: `server/app/render_queue.py:43-312`
- Why fragile: Queued and running render jobs, their progress, and the `_inputs` dict (list-file and output-path) are held only in RAM. A server restart while a render is in progress leaves an orphaned partial output file (`video_dir / stem.mp4`) with no record of the job. The reaper TTL (`RECENT_TTL_SECONDS = 60`) also means completed-job metadata is gone 60 seconds after finishing — too short for a user who navigates away and returns.
- Safe modification: Persist job state to a small JSON file on enqueue/finish; scan for orphaned partial files at startup.
- Test coverage: `tests/server/test_render_queue.py` tests logic but not persistence across restarts.

**`GPHOTO2_STAGE_DIR = Path("/tmp/timelapse-agent-stage")` is a hardcoded global path:**
- Files: `agent/timelapse_agent.py:699`
- Why fragile: On a multi-tenant system, two agent instances with different `camera_id` values would use the same staging directory. The directory is created with `mkdir(parents=True, exist_ok=True)` without per-camera isolation.
- Safe modification: Derive the stage path from `work_dir` (e.g. `work_dir / "stage"`) so it's scoped to the camera's work directory.
- Test coverage: Not tested; stage path is implicit in `upload_camera_pending`.

**`provision_script.py` URL regex rejects IPv6 and uncommon-but-valid hostnames:**
- Files: `server/app/provision_script.py:8`
- Why fragile: `VALID_SERVER_URL_RE = r"^https?://[A-Za-z0-9._-]+(?::\d+)?"` rejects IPv6 addresses (e.g. `http://[::1]:8081`) and hostnames with underscores that are technically invalid DNS but commonly used in local/container setups.
- Safe modification: Use `urllib.parse.urlparse` for structural validation and a separate address check; or document the constraint explicitly.

---

## Scaling Limits

**Single-file JSON store:**
- Current capacity: Works well up to ~50 cameras with ~100 000 total images. Beyond that, `load_store` / `save_store` (reading and rewriting the full JSON on every mutating request) becomes a latency bottleneck.
- Limit: No hard limit, but contention and file I/O time grow linearly with camera count × checkin frequency.
- Scaling path: Migrate to SQLite (stdlib `sqlite3`) with one row per camera; store status separately from config to allow high-frequency checkin writes without touching the config table.

**Render queue bounded by ffmpeg parallelism:**
- Current capacity: `MAX_CONCURRENT_RENDERS = 1`. Only one ffmpeg process runs at a time.
- Limit: A long GIF render (palette + encode) blocks all queued jobs.
- Scaling path: Increase `MAX_CONCURRENT_RENDERS` and add per-camera fairness; or allow the user to cancel and re-queue.

---

## Dependencies at Risk

**`@app.on_event` removal in a future FastAPI release:**
- Risk: FastAPI has deprecated `on_event` in favour of `lifespan`; removal is planned but not scheduled. When removed, the server will fail to start.
- Impact: `RenderRunner` startup/shutdown breaks.
- Migration plan: Straightforward `lifespan` migration; see FastAPI docs.

---

## Missing Critical Features

**No server-side upload size enforcement:**
- Problem: `POST /api/cameras/{id}/upload` has no `Content-Length` or body-size check. A misconfigured or malicious client on the LAN can exhaust disk space by uploading arbitrarily large payloads.
- Blocks: Safe operation on shared or low-capacity storage.

**No disk-space gate on the server before accepting uploads:**
- Problem: The server has `compute_stats` to measure usage but never checks available space before writing an uploaded image. The agent has eviction logic (`evict_pending`) for its local pending directory, but the server has no equivalent.
- Blocks: Long-running unattended deployments on small disks.

**DSLR scene-light gating unavailable on gphoto2 backend:**
- Problem: `should_sample_light` returns `False` for the gphoto2 backend (`agent/timelapse_agent.py:1318-1322`). The Pi camera YUV thumbnail approach has no equivalent for USB DSLRs. `schedule_mode='scene'` is silently a no-op for DSLR cameras.
- Blocks: Daylight-gated timelapse for DSLR users who want scene-light scheduling.

---

## Test Coverage Gaps

**No test for concurrent checkin writes:**
- What's not tested: Two simultaneous `POST /api/cameras/{id}/checkin` requests racing on the same camera record.
- Files: `server/app/main.py:1144-1198`
- Risk: Last-writer-wins data loss for any field updated by both requests simultaneously.
- Priority: Medium

**No test for `GET /api/cameras` performance characteristics:**
- What's not tested: Response time or correctness with O(100+) cameras each having O(1000+) images.
- Files: `server/app/main.py:1079-1086`
- Risk: Silent performance regression as datasets grow.
- Priority: Medium

**No test for server-restart render state loss:**
- What's not tested: Behaviour when the server restarts with a partially-written output file in `DATA_DIR/videos/`.
- Files: `server/app/render_queue.py`
- Risk: Orphaned `.mp4` files silently appear in the video list as completed files.
- Priority: Low

**No test for `GPHOTO2_STAGE_DIR` path collision:**
- What's not tested: Two agents with different `camera_id` values writing to the shared `/tmp/timelapse-agent-stage` simultaneously.
- Files: `agent/timelapse_agent.py:699`
- Risk: Stage files overwrite each other under concurrent upload.
- Priority: Low (single-instance deployments only today)

**No integration test for the full upload → thumbnail → frame-listing cycle:**
- What's not tested: The background thumbnail task is exercised only via direct unit calls in `tests/server/test_frames_listing.py`; the actual `BackgroundTasks` wiring in the upload endpoint is not tested end-to-end.
- Files: `server/app/main.py:1401-1424`
- Risk: A change to the background-task invocation silently breaks thumbnail generation.
- Priority: Low

---

*Concerns audit: 2026-05-08*
