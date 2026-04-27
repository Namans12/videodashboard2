from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import UploadFile as StarletteUploadFile
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import List
import logging
import shutil
import os
import uuid

from analysis import analyze_file, scan_folder

app = FastAPI(title="Video Metadata Analyzer")
logger = logging.getLogger("video-analyzer.api")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_upload(file: UploadFile) -> str:
    original_name = os.path.basename(file.filename or "upload.bin")
    unique_name   = f"{uuid.uuid4().hex}_{original_name}"
    file_path     = os.path.join(UPLOAD_DIR, unique_name)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return file_path


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


# ── Single file upload ───────────────────────────────────────────────────────

@app.post("/analysis/")
async def analyze_video(request: Request, fast: bool = Query(False)):
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type.lower():
        raise HTTPException(status_code=400,
                            detail="Upload must use multipart/form-data with a file field.")
    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid multipart body: {exc}") from exc

    file = form.get("file") or form.get("upload") or form.get("video")
    if not isinstance(file, (UploadFile, StarletteUploadFile)):
        raise HTTPException(status_code=400, detail="No uploaded file found. Use field name 'file'.")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file has no filename.")

    file_path = save_upload(file)   # type: ignore
    try:
        result = analyze_file(file_path, skip_dovi_scan=fast)
    finally:
        try: os.remove(file_path)
        except OSError: pass

    if not result:
        raise HTTPException(status_code=400, detail="Unable to analyze the selected file.")

    result["batch_rank"] = 1
    return [result]


# ── Multiple file upload ─────────────────────────────────────────────────────

@app.post("/analyze-multiple/")
async def analyze_multiple(
    files: List[UploadFile] = File(...),
    fast: bool = Query(False),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    saved_paths: list[str] = []
    try:
        for f in files:
            if not f.filename:
                continue
            saved_paths.append(save_upload(f))

        if not saved_paths:
            raise HTTPException(status_code=400, detail="All uploaded files were rejected.")

        results: list = []
        worker = partial(analyze_file, skip_dovi_scan=fast)

        with ThreadPoolExecutor(max_workers=min(4, len(saved_paths))) as executor:
            future_map = {executor.submit(worker, path): path for path in saved_paths}
            for future in as_completed(future_map):
                result = future.result()
                if result:
                    results.append(result)
    finally:
        for path in saved_paths:
            try: os.remove(path)
            except OSError: pass

    if not results:
        raise HTTPException(status_code=400, detail="No files could be analyzed.")

    return rank_results(results)


# ── Path / folder endpoints ──────────────────────────────────────────────────

@app.get("/analyze-path/")
def analyze_video_path(path: str, fast: bool = Query(False)):
    path = path.strip().strip('"')
    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail="File does not exist.")
    result = analyze_file(path, skip_dovi_scan=fast)
    if not result:
        raise HTTPException(status_code=400, detail="Unable to analyze the selected file.")
    result["batch_rank"] = 1
    return [result]


@app.get("/scan-folder/")
def scan_folder_api(path: str, fast: bool = Query(False)):
    path = path.strip().strip('"')
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Folder does not exist.")
    results = scan_folder(path, skip_dovi_scan=fast)
    if not results:
        raise HTTPException(status_code=404, detail="No supported video files found in this folder.")
    return results