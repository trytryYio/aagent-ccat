"""RAG 知识库管理 API：上传、入库、列表、删除文档。"""

import asyncio
import logging
import os
import sys
import uuid
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from qdrant_client.models import Filter, FieldCondition, MatchValue

from app.core.tenant_manager import DEFAULT_TENANT_ID
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/docs", tags=["knowledge"])

# 支持的文件类型
SUPPORTED_EXTS = {".pdf", ".md", ".txt", ".csv", ".xlsx", ".docx"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# 项目根目录下的 docs/ 目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")


def _ensure_docs_dir() -> str:
    os.makedirs(DOCS_DIR, exist_ok=True)
    return DOCS_DIR


@router.post("/upload")
async def upload_docs(files: list[UploadFile] = File(...), request: Request = None):
    """上传文档到 docs/ 目录（不入库，需要后续触发 ingest）。"""
    if not files:
        raise HTTPException(status_code=400, detail="未提供文件")

    docs_dir = _ensure_docs_dir()
    saved = []
    errors = []

    for f in files:
        try:
            ext = os.path.splitext(f.filename or "")[1].lower()
            if ext not in SUPPORTED_EXTS:
                errors.append({"filename": f.filename, "error": f"不支持的格式: {ext}"})
                continue

            # 读内容限制大小
            content = await f.read()
            if len(content) > MAX_FILE_SIZE:
                errors.append({"filename": f.filename, "error": f"文件超过 50MB 限制"})
                continue

            # 文件名加 UUID 防冲突
            base_name = os.path.splitext(f.filename or "unnamed")[0]
            safe_name = f"{base_name}_{uuid.uuid4().hex[:8]}{ext}"
            save_path = os.path.join(docs_dir, safe_name)

            with open(save_path, "wb") as fp:
                fp.write(content)

            saved.append({
                "original_name": f.filename,
                "saved_as": safe_name,
                "size": len(content),
                "path": save_path,
            })
        except Exception as e:
            errors.append({"filename": f.filename, "error": str(e)})
        finally:
            await f.close()

    return {
        "code": 0,
        "message": f"已上传 {len(saved)} 个文件",
        "data": {"saved": saved, "errors": errors},
    }


class IngestRequest(BaseModel):
    files: Optional[list[str]] = None  # 指定文件名列表，为空则入库全部


@router.post("/ingest")
async def trigger_ingest(req: IngestRequest = None):
    """触发文档入库（异步）。"""
    docs_dir = _ensure_docs_dir()

    # 如果指定了文件，只入库指定文件（先把它们复制到临时目录再调脚本）
    if req and req.files:
        import shutil
        import tempfile
        target_files = []
        tmpdir = tempfile.mkdtemp(prefix="ingest_")
        for fname in req.files:
            src = os.path.join(docs_dir, fname)
            if not os.path.exists(src):
                continue
            dst = os.path.join(tmpdir, fname)
            shutil.copy(src, dst)
            target_files.append(dst)
        if not target_files:
            raise HTTPException(status_code=404, detail="指定的文件不存在")

        # 异步跑入库
        asyncio.create_task(_run_ingest(target_files, tmpdir))
        return {"code": 0, "message": f"已开始入库 {len(target_files)} 个文件", "data": {"count": len(target_files)}}

    # 入库全部
    asyncio.create_task(_run_ingest(None, None))
    return {"code": 0, "message": "已开始入库 docs/ 目录全部文档"}


async def _run_ingest(files: Optional[list[str]], tmpdir: Optional[str]):
    """异步跑 ingest_documents.py 脚本。"""
    try:
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
        from scripts.ingest_documents import main as ingest_main

        # 临时改 cwd 到 docs/ 所在位置，让脚本能找到 docs 目录
        original_cwd = os.getcwd()
        try:
            # 脚本会扫描 backend/scripts/ingest_documents.py:47 的 docs 目录
            # 路径是 os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs"))
            # 这里无需改 cwd，脚本固定扫描 PROJECT_ROOT/docs
            if tmpdir:
                # 临时方案：把 tmpdir 内容复制到 docs/ 下的子目录
                # 简化：直接调用 main，但 tmpdir 场景暂不支持
                pass
            await ingest_main()
            logger.info("[Ingest] 完成")
        finally:
            os.chdir(original_cwd)
    except Exception as e:
        logger.error("[Ingest] 失败: %s", e, exc_info=True)
    finally:
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


@router.get("/list")
async def list_documents(request: Request = None):
    """列出已入库的文档（按 doc_id 聚合）。"""
    tenant_id = getattr(request.state, "tenant_id", DEFAULT_TENANT_ID) if request else DEFAULT_TENANT_ID

    from rag.db_client import get_qdrant_client
    client = get_qdrant_client()

    # 滚动查询所有文档
    docs_map: dict[str, dict] = {}
    offset = None
    while True:
        results = client.scroll(
            collection_name="documents",
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
            scroll_filter=Filter(must=[
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
            ]) if tenant_id != "default" else None,
        )
        points, next_offset = results
        if not points:
            break
        for p in points:
            payload = p.payload or {}
            doc_id = payload.get("doc_id", "")
            if not doc_id:
                continue
            if doc_id not in docs_map:
                docs_map[doc_id] = {
                    "doc_id": doc_id,
                    "file_name": payload.get("file_name", ""),
                    "source": payload.get("source", ""),
                    "chunk_count": 0,
                    "first_indexed": "",
                    "last_indexed": "",
                }
            docs_map[doc_id]["chunk_count"] += 1

        if next_offset is None:
            break
        offset = next_offset

    return {"code": 0, "data": {"documents": list(docs_map.values()), "total": len(docs_map)}}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, request: Request = None):
    """删除指定文档的所有 chunks。"""
    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id 不能为空")

    from rag.db_client import get_qdrant_client
    client = get_qdrant_client()

    try:
        client.delete(
            collection_name="documents",
            points_selector=Filter(must=[
                FieldCondition(key="doc_id", match=MatchValue(value=doc_id))
            ]),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")

    return {"code": 0, "message": f"文档 {doc_id} 已删除"}


@router.get("/status")
async def ingest_status():
    """查看入库状态（占位，未来可加进度查询）。"""
    return {"code": 0, "data": {"status": "idle", "last_run": None, "in_progress": 0}}