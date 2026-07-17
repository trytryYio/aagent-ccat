import uuid
import os
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.config import settings

router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "images")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = set(settings.allowed_extensions.split(","))


@router.post("/api/v1/upload/image")
async def upload_image(file: UploadFile = File(...)):
    ext = file.filename.split(".")[-1].lower() if file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail={
            "code": "INVALID_FORMAT",
            "message": f"仅支持 {settings.allowed_extensions} 格式"
        })

    content = await file.read()
    if len(content) > settings.max_image_size:
        raise HTTPException(status_code=413, detail={
            "code": "FILE_TOO_LARGE",
            "message": "图片大小超过 10MB 限制"
        })

    image_id = uuid.uuid4().hex
    filename = f"{image_id}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(content)

    return {
        "code": 0,
        "message": "success",
        "data": {
            "image_id": image_id,
            "url": f"/data/images/{filename}",
            "size": len(content)
        }
    }
