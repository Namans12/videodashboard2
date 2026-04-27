from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import UploadFile as StarletteUploadFile
import logging
import shutil
import os
import uuid
from analysis import analyze_file

app = FastAPI()
logger = logging.getLogger("video-dashboard.api")

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
    unique_name = f"{uuid.uuid4().hex}_{original_name}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return file_path


@app.post("/analysis/")
async def analyze_video(request: Request):
    content_type = request.headers.get("content-type", "")
    logger.info("/analysis upload start content-type=%s", content_type)

    if "multipart/form-data" not in content_type.lower():
        logger.warning("/analysis rejected: non-multipart request")
        raise HTTPException(
            status_code=400,
            detail="Upload must use multipart/form-data with a file field.",
        )

    try:
        form = await request.form()
    except Exception as exc:
        logger.warning("/analysis multipart parse failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Invalid multipart body: {exc}") from exc

    logger.info("/analysis form keys: %s", list(form.keys()))
    file = form.get("file") or form.get("upload") or form.get("video")
    if not isinstance(file, (UploadFile, StarletteUploadFile)):
        logger.warning("/analysis rejected: missing upload field in form")
        raise HTTPException(
            status_code=400,
            detail="No uploaded file found. Use field name 'file'.",
        )

    if not file.filename:
        logger.warning("/analysis rejected: uploaded file has empty filename")
        raise HTTPException(status_code=400, detail="Uploaded file has no filename.")

    file_path = save_upload(file) # type: ignore

    try:
        result = analyze_file(file_path)
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass

    if not result:
        raise HTTPException(status_code=400, detail="Unable to analyze the selected file.")

    return [result]


@app.get("/analyze-path/")
def analyze_video_path(path: str, fast: bool = False):
    path = path.strip().strip('"')
    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail="File does not exist.")

    result = analyze_file(path)
    if not result:
        raise HTTPException(status_code=400, detail="Unable to analyze the selected file.")

    result = analyze_file(path, skip_dovi_scan=fast)


@app.get("/scan-folder/")
def scan_folder_api(path: str, fast: bool = False):
    from analysis import scan_folder

    path = path.strip().strip('"')
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Folder does not exist.")

    return scan_folder(path, skip_dovi_scan=fast)
