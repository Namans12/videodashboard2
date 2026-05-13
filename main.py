import errno
import shutil
import os
import uuid
import logging
import threading
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import List
import time
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
import asyncio
import json
import urllib.request
import urllib.error
from urllib.parse import urlparse
import re

# Optional: libtorrent for magnet link support
try:
    import libtorrent as lt
    LIBTORRENT_AVAILABLE = True
except ImportError:
    LIBTORRENT_AVAILABLE = False
    lt = None

from analysis import analyze_file, scan_folder

logger = logging.getLogger("video-analyzer.api")

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_UPLOAD_BYTES = 120 * 1024 * 1024 * 1024    # 120 GB
MIN_FREE_BYTES   = 20 * 1024 * 1024 * 1024  # 20 GB
PARTIAL_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB for HTTP range requests

_jobs: dict[str, dict] = {}


def _cleanup_old_jobs() -> None:
    """Keep only the 20 most recent completed jobs."""
    done = [jid for jid, job in _jobs.items() if job["status"] in {"done", "error"}]
    for jid in done[:-20]:
        _jobs.pop(jid, None)


def download_magnet_torrent(magnet_url: str, max_bytes: int = PARTIAL_DOWNLOAD_SIZE, timeout_seconds: int = 120) -> tuple[str, str]:
    """
    Download a partial torrent file from a magnet link.
    Downloads first ~100 MB of pieces for metadata extraction.
    
    Args:
        magnet_url: Magnet link URI
        max_bytes: Maximum bytes to download (default: 100 MB)
        timeout_seconds: Max time to wait for download (default: 120 sec)
    
    Returns:
        Tuple of (file_path, original_filename)
    
    Raises:
        HTTPException: On network or parsing errors
    """
    if not LIBTORRENT_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="Magnet link support requires libtorrent library (optional). "
                   "See MAGNET_SETUP.md for installation instructions. "
                   "For now, use HTTP/HTTPS URLs for video analysis."
        )
    
    import re
    import time as time_module
    
    try:
        # Parse magnet link to extract metadata
        session = lt.session()
        params = lt.parse_magnet_uri(magnet_url)
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        
        if not params.info_hash:
            raise HTTPException(
                status_code=400,
                detail="Invalid magnet link - no info hash found"
            )
        
        # Extract filename from magnet dn parameter
        dn_match = re.search(r'&dn=([^&]+)', magnet_url)
        filename = None
        if dn_match:
            filename = urllib.parse.unquote(dn_match.group(1))
        
        if not filename:
            filename = params.info_hash.hex()[:16] + ".download"
        
        # Ensure filename has video extension
        if not any(filename.lower().endswith(ext) for ext in ('.mkv', '.mp4', '.ts', '.m2ts', '.hevc', '.h265', '.mov', '.avi', '.flv')):
            if '.' not in filename.split('/')[-1]:  # No extension at all
                filename = filename + '.mp4'
        
        logger.info(f"Magnet: Starting download for {filename} ({params.info_hash.hex()[:16]}...)")
        
        # Create temp directory for torrent download
        download_dir = os.path.join(UPLOAD_DIR, "magnet_temp", params.info_hash.hex()[:8])
        os.makedirs(download_dir, exist_ok=True)
        
        # Configure session for faster metadata retrieval
        settings = lt.session_settings()
        settings.announce_to_all_tiers = True
        settings.announce_to_all_trackers = True
        settings.prioritize_partial_pieces = True
        session.set_settings(settings)
        
        # Set save path and add torrent
        params.save_path = download_dir
        handle = session.add_torrent(params)
        
        logger.info(f"Magnet: Added to session, waiting for metadata...")
        
        # Wait for metadata (has_metadata)
        metadata_timeout = 30
        metadata_start = time_module.time()
        while not handle.has_metadata() and (time_module.time() - metadata_start) < metadata_timeout:
            time_module.sleep(0.5)
        
        if not handle.has_metadata():
            logger.warning(f"Magnet: Metadata not received within {metadata_timeout}s")
            raise HTTPException(
                status_code=408,
                detail=f"Magnet link metadata timeout after {metadata_timeout}s. Try a different torrent or use HTTP URL."
            )
        
        logger.info(f"Magnet: Metadata received, starting download")
        
        # Get torrent file info
        torrent_file = handle.torrent_file()
        if not torrent_file:
            raise HTTPException(status_code=500, detail="Could not get torrent file info")
        
        torrent_files = torrent_file.files()
        if not torrent_files:
            raise HTTPException(status_code=500, detail="No files in torrent")
        
        # Find main video file (largest one)
        main_file_idx = 0
        main_file_size = 0
        main_file_obj = None
        
        for i, f in enumerate(torrent_files):
            if f.size > main_file_size:
                main_file_idx = i
                main_file_size = f.size
                main_file_obj = f
        
        if not main_file_obj:
            raise HTTPException(status_code=500, detail="No files found in torrent")
        
        logger.info(f"Magnet: Main file: {main_file_obj.path.decode('utf-8') if isinstance(main_file_obj.path, bytes) else main_file_obj.path} ({main_file_size / 1024 / 1024:.1f} MB)")
        
        # Set priority to download main file first
        for i in range(torrent_files.num_files()):
            if i == main_file_idx:
                handle.file_priority(i, 7)  # Highest priority
            else:
                handle.file_priority(i, 0)  # Don't download
        
        # Force start downloading
        handle.resume()
        
        # Download loop - get ~100 MB
        logger.info(f"Magnet: Downloading {max_bytes / 1024 / 1024:.0f} MB for analysis...")
        
        start_time = time_module.time()
        bytes_downloaded = 0
        last_log = 0
        pieces_to_prioritize = int((max_bytes / main_file_size) * torrent_files.file_size(main_file_idx)) + 10
        
        # Prioritize first pieces
        num_pieces = handle.get_torrent_info().num_pieces()

        # Prioritize first N pieces (video data start)
        for i in range(min(pieces_to_prioritize, num_pieces)):
            handle.piece_priority(i, 7)

        # Also prioritize LAST 15 pieces — MP4 moov atom lives at the end.
        # Without these, ffprobe cannot read stream metadata from a partial MP4.
        # MKV files are unaffected (metadata is at the start).
        moov_piece_count = min(15, num_pieces)
        moov_start_piece = num_pieces - moov_piece_count
        for i in range(moov_start_piece, num_pieces):
            handle.piece_priority(i, 7)
        logger.info(f"Magnet: Prioritized first {pieces_to_prioritize} + last {moov_piece_count} pieces for moov atom")

        while (time_module.time() - start_time) < timeout_seconds:
            status = handle.status()
            bytes_downloaded = status.total_wanted_done
            pieces_bits = status.pieces

            # Check if last (moov) pieces are all downloaded
            moov_done = all(
                (pieces_bits[i] if i < len(pieces_bits) else False)
                for i in range(moov_start_piece, num_pieces)
            )

            # Log progress
            if bytes_downloaded > last_log + 20*1024*1024:  # Log every 20 MB
                percent = int((bytes_downloaded / max_bytes) * 100) if max_bytes > 0 else 0
                logger.info(f"Magnet download: {bytes_downloaded / 1024 / 1024:.1f} MB ({percent}%) | moov={'done' if moov_done else 'pending'}")
                last_log = bytes_downloaded

            # Stop once we have enough front data AND the moov atom
            if bytes_downloaded >= max_bytes and moov_done:
                logger.info(f"Downloaded {bytes_downloaded / 1024 / 1024:.1f} MB + moov atom, stopping")
                break

            time_module.sleep(1)
        
        # Pause and find the actual file
        handle.pause()
        time_module.sleep(1)
        session.remove_torrent(handle)   # ← release file handles
        time_module.sleep(1)

        # Now safe to copy and delete
        shutil.copy2(main_file_path, final_path)

        try:
            shutil.rmtree(download_dir)
        except Exception as e:
            logger.warning(f"Could not cleanup {download_dir}: {e}")

        # Construct path to main file
        main_file_path_parts = main_file_obj.path
        if isinstance(main_file_path_parts, bytes):
            main_file_path_parts = main_file_path_parts.decode('utf-8')
        
        main_file_path = os.path.join(download_dir, main_file_path_parts)
        
        logger.info(f"Magnet: Looking for file at {main_file_path}")
        
        # Give it a moment to write
        time_module.sleep(2)
        
        if not os.path.exists(main_file_path):
            # Try to find any .mp4, .mkv file
            for root, dirs, files in os.walk(download_dir):
                for f in files:
                    if any(f.lower().endswith(ext) for ext in ('.mkv', '.mp4', '.ts', '.m2ts', '.hevc', '.h265', '.mov', '.avi', '.flv')):
                        main_file_path = os.path.join(root, f)
                        logger.info(f"Magnet: Found file at {main_file_path}")
                        break
        
        if not os.path.exists(main_file_path):
            raise HTTPException(
                status_code=500,
                detail=f"Downloaded file not found. Path: {main_file_path}"
            )
        
        # Copy to uploads directory with standard naming
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        final_path = os.path.join(UPLOAD_DIR, unique_name)
        
        file_size_actual = os.path.getsize(main_file_path)
        logger.info(f"Magnet: Copying {file_size_actual / 1024 / 1024:.1f} MB to {final_path}")
        
        shutil.copy2(main_file_path, final_path)
        
        # Cleanup
        try:
            shutil.rmtree(download_dir)
        except Exception as e:
            logger.warning(f"Could not cleanup {download_dir}: {e}")
        
        logger.info(f"Magnet: Download complete - {final_path}")
        return final_path, filename
    
    except HTTPException:
        raise
    except (RuntimeError, Exception):
        raise HTTPException(
            status_code=400,
            detail="Invalid magnet link - could not create torrent handle"
        )
    except Exception as e:
        logger.exception(f"Magnet download failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Magnet download error: {str(e)}"
        )


def download_partial_video(url: str, max_bytes: int = PARTIAL_DOWNLOAD_SIZE) -> tuple[str, str]:
    """
    Download a partial video file using HTTP Range requests.
    Returns (file_path, original_filename).
    Only downloads first `max_bytes` to extract metadata efficiently.
    """
    url = url.strip()
    
    # Handle magnet links
    if url.lower().startswith('magnet:'):
        logger.info(f"Detected magnet link, starting torrent download")
        return download_magnet_torrent(url, max_bytes)
    
    if not url.lower().startswith(('http://', 'https://')):
        raise HTTPException(
            status_code=400,
            detail="URL must be HTTP, HTTPS, or magnet link (magnet:?xt=...)"
        )
    
    try:
        # Try to get file size and support for range requests
        req = urllib.request.Request(url, method='HEAD')
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            content_length = response.headers.get('Content-Length')
            accept_ranges = response.headers.get('Accept-Ranges', '').lower()
            file_size = int(content_length) if content_length else None
            supports_range = accept_ranges == 'bytes'
            
            # Extract filename from URL or Content-Disposition header
            content_disp = response.headers.get('Content-Disposition', '')
            filename = None
            if 'filename=' in content_disp:
                filename = content_disp.split('filename=')[-1].strip('"\'')
            if not filename:
                parsed_url = urlparse(url)
                filename = os.path.basename(parsed_url.path) or 'video'
                if not any(filename.lower().endswith(ext) for ext in ('.mkv', '.mp4', '.ts', '.m2ts', '.hevc', '.h265')):
                    filename = filename + '.mp4'  # Default extension
    
    except urllib.error.URLError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not access URL: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error checking URL headers: {str(e)}"
        )
    
    # Download partial file using Range if supported
    try:
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        file_path = os.path.join(UPLOAD_DIR, unique_name)
        bytes_downloaded = 0
        chunk_size = 1024 * 1024  # 1 MB chunks
        
        if supports_range and file_size and file_size > max_bytes:
            # Use Range header to download only first chunk
            range_header = f'bytes=0-{max_bytes-1}'
            req = urllib.request.Request(url)
            req.add_header('Range', range_header)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            logger.info(f"Downloading with range: {range_header} for {filename}")
        else:
            # No range support or file smaller than limit - download what we can
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            if file_size:
                logger.info(f"Range not supported or small file. File size: {file_size / 1024 / 1024:.1f} MB")
        
        with urllib.request.urlopen(req, timeout=60) as response:
            with open(file_path, 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_downloaded += len(chunk)
                    
                    # Stop if we've downloaded enough
                    if bytes_downloaded >= max_bytes:
                        logger.info(f"Downloaded {bytes_downloaded / 1024 / 1024:.1f} MB for metadata analysis")
                        break
        
        logger.info(f"Downloaded {bytes_downloaded / 1024 / 1024:.1f} MB from {filename}")
        return file_path, filename
    
    except Exception as e:
        try:
            os.remove(file_path) if os.path.exists(file_path) else None
        except OSError:
            pass
        raise HTTPException(
            status_code=400,
            detail=f"Error downloading video from URL: {str(e)}"
        )

def _patch_result_path(result: dict, temp_path: str, orig_name: str) -> None:
    result["file"] = orig_name
    temp_base = os.path.basename(temp_path)
    if "path" in result and temp_base in result["path"]:
        result["path"] = orig_name.join(result["path"].rsplit(temp_base, 1))


@asynccontextmanager
async def lifespan(app: FastAPI):
    for f in os.listdir(UPLOAD_DIR):
        try:
            os.remove(os.path.join(UPLOAD_DIR, f))
        except OSError:
            pass
    logger.info("Upload dir cleaned on startup.")
    yield


app = FastAPI(title="Video Metadata Analyzer", lifespan=lifespan)

class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_UPLOAD_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Request too large — max {MAX_UPLOAD_BYTES // (1024**3)} GB."}
                )
        return await call_next(request)

app.add_middleware(MaxBodySizeMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def check_disk_space() -> None:
    try:
        usage = shutil.disk_usage(UPLOAD_DIR)
        if usage.free < MIN_FREE_BYTES:
            free_gb = usage.free / 1_073_741_824
            raise HTTPException(
                status_code=507,
                detail=f"Server storage almost full ({free_gb:.1f} GB free). "
                       "Clean the uploads/ folder and retry.",
            )
    except HTTPException:
        raise
    except OSError:
        pass


def _run_batch_job(job_id: str, saved_paths: list[str],
                   path_name_map: dict[str, str], fast: bool) -> None:
    job    = _jobs[job_id]
    total  = len(saved_paths)
    done   = 0
    results: list = []
    worker = partial(analyze_file, skip_dovi_scan=fast)

    def emit(msg: str) -> None:
        job["events"].append({"msg": msg, "ts": time.time()})
        job["progress"] = f"{done}/{total}"

    emit(f"Starting analysis of {total} file(s)…")

    with ThreadPoolExecutor(max_workers=min(4, total)) as executor:
        future_map = {
            executor.submit(worker, path): (path, path_name_map[path])
            for path in saved_paths
        }
        for future in as_completed(future_map):
            temp_path, orig_name = future_map[future]
            job["current"] = orig_name
            try:
                result = future.result()
                if result:
                    _patch_result_path(result, temp_path, orig_name)
                    results.append(result)
                    emit(f"✓ {orig_name}")
                else:
                    emit(f"✗ {orig_name} — no data extracted")
            except Exception as exc:
                logger.warning("Analysis failed for %s: %s", orig_name, exc)
                emit(f"✗ {orig_name} — {exc}")
            finally:
                done += 1
                job["progress"] = f"{done}/{total}"
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    if results:
        job["results"] = rank_results(results)
        job["status"]  = "done"
        emit(f"Done — {len(results)}/{total} analysed successfully.")
    else:
        job["status"] = "error"
        job["error"]  = "No files could be analyzed."
        emit("Error: no files could be analysed.")
    threading.Timer(30.0, _cleanup_old_jobs).start()


def save_upload(file: UploadFile) -> tuple[str, str]:
    original_name = os.path.basename(file.filename or "upload.bin")
    
    # For large files, tell user to use path input instead
    # (uvicorn rejects huge uploads before we can even check size)
    # This message shows for files where content-length header is present
    content_length = getattr(file, 'size', None)
    if content_length and content_length > 10 * 1024 * 1024 * 1024:  # 10 GB
        raise HTTPException(
            status_code=400,
            detail=(
                f"File too large to upload ({content_length // (1024**3):.0f} GB). "
                "Use the path input instead — paste the full file path or folder path "
                "directly into the text box. No upload needed for local files."
            )
        )
    unique_name   = f"{uuid.uuid4().hex}_{original_name}"
    file_path     = os.path.join(UPLOAD_DIR, unique_name)
    bytes_written = 0
    chunk_size    = 1024 * 1024  # 1 MB
    try:
        with open(file_path, "wb") as buffer:
            while True:
                chunk = file.file.read(chunk_size)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    buffer.close()
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large — max {MAX_UPLOAD_BYTES // (1024*1024)} MB allowed.",
                    )
                if bytes_written % (50 * 1024 * 1024) == 0:
                    if shutil.disk_usage(UPLOAD_DIR).free < MIN_FREE_BYTES:
                        buffer.close()
                        try:
                            os.remove(file_path)
                        except OSError:
                            pass
                        raise HTTPException(
                            status_code=507,
                            detail="Server ran out of disk space during upload.",
                        )
                buffer.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        try:
            os.remove(file_path)
        except OSError:
            pass
        if exc.errno == errno.ENOSPC:
            raise HTTPException(
                status_code=507,
                detail="Server disk is full. Free up space and retry.",
            )
        raise HTTPException(status_code=500, detail=f"Could not save upload: {exc}")
    finally:
        try:
            file.file.close()
        except Exception:
            pass
    return file_path, original_name


def rank_results(results: list) -> list:
    results.sort(
        key=lambda x: (
            x.get("tv_score", 0),
            x.get("score", 0),
            x.get("confidence_score", 0),
            x.get("bitrate_mbps", 0),
            x.get("file", ""),
        ),
        reverse=True,
    )
    for idx, item in enumerate(results, 1):
        item["batch_rank"] = idx
    return results


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health/")
def health_check():
    usage = shutil.disk_usage(UPLOAD_DIR)
    return {
        "status":       "ok",
        "disk_free_gb": round(usage.free  / 1_073_741_824, 2),
        "disk_used_gb": round(usage.used  / 1_073_741_824, 2),
        "upload_dir":   os.path.abspath(UPLOAD_DIR),
    }


@app.post("/analysis/")
async def analyze_video(background_tasks: BackgroundTasks, request: Request, fast: bool = Query(False)):
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type.lower():
        raise HTTPException(status_code=400,
                            detail="Upload must use multipart/form-data with a file field.")
    check_disk_space()

    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid multipart body: {exc}") from exc

    file = form.get("file") or form.get("upload") or form.get("video")
    if not isinstance(file, (UploadFile, StarletteUploadFile)):
        raise HTTPException(status_code=400, detail="No uploaded file found. Use field name 'file'.")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file has no filename.")

    temp_path, orig_name = save_upload(file)   # type: ignore[arg-type]

    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "status": "running", "progress": "0/1",
        "current": orig_name, "total": 1,
        "results": [], "error": None, "events": [],
    }

    def _run_single(jid: str, path: str, name: str, f: bool) -> None:
        job = _jobs[jid]
        job["events"].append({"msg": f"Analyzing {name}…", "ts": time.time()})
        try:
            result = analyze_file(path, skip_dovi_scan=f)
            if result:
                _patch_result_path(result, path, name)
                result["batch_rank"] = 1
                job["results"] = [result]
            job["status"] = "done"
            job["events"].append({"msg": f"✓ {name}", "ts": time.time()})
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
            threading.Timer(30.0, _cleanup_old_jobs).start()

    background_tasks.add_task(_run_single, job_id, temp_path, orig_name, fast)
    return {"job_id": job_id, "total": 1}


@app.post("/analyze-multiple/")
async def analyze_multiple(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    fast: bool = Query(False),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    check_disk_space()

    saved_paths:   list[str]      = []
    path_name_map: dict[str, str] = {}

    for f in files:
        if not f.filename:
            continue
        temp_path, orig_name = save_upload(f)
        saved_paths.append(temp_path)
        path_name_map[temp_path] = orig_name

    if not saved_paths:
        raise HTTPException(status_code=400, detail="All uploaded files were rejected.")

    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "status": "running", "progress": f"0/{len(saved_paths)}",
        "current": "", "total": len(saved_paths),
        "results": [], "error": None, "events": [],
    }
    background_tasks.add_task(_run_batch_job, job_id, saved_paths, path_name_map, fast)
    return {"job_id": job_id, "total": len(saved_paths)}


@app.get("/analyze-path/")
def analyze_video_path(background_tasks: BackgroundTasks, path: str, fast: bool = Query(False)):
    """Accept a server-local file or folder path and run analysis as a background job.
    Returns a `job_id` immediately and emits progress via the existing SSE `/progress/{job_id}` endpoint.
    """
    path = path.strip().strip('"')
    if not (os.path.isfile(path) or os.path.isdir(path)):
        raise HTTPException(status_code=400, detail="File or folder does not exist.")

    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "status": "running",
        "progress": "0/1",
        "current": "",
        "total": 1,
        "results": [],
        "error": None,
        "events": [],
    }

    background_tasks.add_task(_run_path_job, job_id, path, fast)

    return {"job_id": job_id}


def _run_path_job(job_id: str, path: str, fast: bool) -> None:
    job = _jobs[job_id]

    def emit(msg: str) -> None:
        job["events"].append({"msg": msg, "ts": time.time()})

    emit("Job started")

    results = []
    if os.path.isdir(path):
        emit(f"Scanning folder: {os.path.basename(path)}…")
        try:
            file_paths = []
            for root, _, files in os.walk(path):
                for f in files:
                    if f.lower().endswith((".mkv", ".mp4", ".ts", ".m2ts", ".hevc", ".h265")):
                        file_paths.append(os.path.join(root, f))
            
            job["total"] = len(file_paths)
            worker = partial(analyze_file, skip_dovi_scan=fast)
            
            with ThreadPoolExecutor(max_workers=min(4, len(file_paths))) as executor:
                future_map = {executor.submit(worker, p): p for p in file_paths}
                done = 0
                for future in as_completed(future_map):
                    p = future_map[future]
                    job["current"] = os.path.basename(p)
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                            emit(f"✓ {os.path.basename(p)}")
                        else:
                            emit(f"✗ {os.path.basename(p)} — no data")
                    except Exception as exc:
                        logger.warning("scan_folder: failed on %s — %s", p, exc)
                        emit(f"✗ {os.path.basename(p)} — {exc}")
                    finally:
                        done += 1
                        job["progress"] = f"{done}/{len(file_paths)}"
        except Exception as exc:
            logger.warning("Failed scanning folder %s — %s", path, exc)
            job["error"] = str(exc)
            emit(f"Error scanning folder: {exc}")
    else:
        base = os.path.basename(path)
        emit(f"Analyzing {base}…")
        try:
            result = analyze_file(path, skip_dovi_scan=fast)
            if result:
                results.append(result)
                emit(f"✓ {base}")
            else:
                emit(f"✗ {base} — no data extracted")
        except Exception as exc:
            logger.warning("Failed analyzing %s — %s", path, exc)
            job["error"] = str(exc)
            emit(f"Error: {exc}")

    job["results"] = rank_results(results)
    job["status"]  = "done"
    emit("Done.")
    threading.Timer(30.0, _cleanup_old_jobs).start()


@app.get("/analyze-url/")
def analyze_video_url(background_tasks: BackgroundTasks, url: str, fast: bool = Query(False)):
    """Download and analyze a video from a URL or magnet link.
    
    For HTTP/HTTPS: Downloads only the first 100 MB to extract metadata efficiently.
    For magnet links: Connects to DHT and downloads first ~100 MB of torrent pieces.
    
    Returns a `job_id` immediately and emits progress via the existing SSE endpoint.
    
    Supports:
    - HTTP/HTTPS URLs with range request support
    - Magnet links (magnet:?xt=urn:btih:...) via libtorrent
    - Partial download (~100 MB) for fast metadata extraction
    """
    check_disk_space()
    
    url = url.strip()
    logger.info(f"Analyze URL requested: {url[:100]}...")
    
    # Download partial video file
    temp_path, orig_name = download_partial_video(url)
    
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "status": "running",
        "progress": "0/1",
        "current": orig_name,
        "total": 1,
        "results": [],
        "error": None,
        "events": [],
    }
    
    def _run_url_job(jid: str, path: str, name: str, f: bool) -> None:
        job = _jobs[jid]
        job["events"].append({"msg": f"Analyzing partial download: {name}…", "ts": time.time()})
        try:
            logger.info(f"DEBUG: analyze_file path={path}, exists={os.path.exists(path)}, size={os.path.getsize(path) if os.path.exists(path) else 'N/A'}")  # ← add this 
            result = analyze_file(path, skip_dovi_scan=f)
            logger.info(f"DEBUG: analyze_file result={result is not None}")
            if result:
                _patch_result_path(result, path, name)
                result["batch_rank"] = 1
                result["source_type"] = "url_partial"
                job["results"] = [result]
            job["status"] = "done"
            job["events"].append({"msg": f"✓ {name} (100 MB sample analyzed)", "ts": time.time()})
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)
            logger.exception("URL analysis failed for %s", name)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
            threading.Timer(30.0, _cleanup_old_jobs).start()
    
    background_tasks.add_task(_run_url_job, job_id, temp_path, orig_name, fast)
    return {"job_id": job_id, "total": 1, "note": "Analyzing partial download (first 100 MB)"}



@app.get("/scan-folder/")
def scan_folder_api(path: str, fast: bool = Query(False)):
    path = path.strip().strip('"')
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Folder does not exist.")
    results = scan_folder(path, skip_dovi_scan=fast)
    if not results:
        raise HTTPException(status_code=404, detail="No supported video files found in this folder.")
    return results


@app.get("/job/{job_id}")
def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/job/{job_id}/events")
async def stream_job_events(job_id: str):
    import asyncio
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def event_gen():
        seen = 0
        while True:
            events = job.get("events", [])
            while seen < len(events):
                yield f"data: {json.dumps(events[seen])}\n\n"
                seen += 1
            if job["status"] in {"done", "error"}:
                yield "data: __done__\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})