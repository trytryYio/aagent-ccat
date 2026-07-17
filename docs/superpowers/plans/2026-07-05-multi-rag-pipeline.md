# 多 RAG 链路（商品 + 文档问答）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有商品推荐链路作为 product_rag chain，新增通用文档问答 docs_rag chain，通过顶层意图路由自动分发。

**Architecture:** 单 LangGraph StateGraph + intent_recognition 一次性路由 + 共享 generate/reflection/finalize。新增 docs_search_node / docs_retrieve_node，复用 BaseRetriever / PostprocessorPipeline / Prompt 外置 / pipeline_step SSE。

**Tech Stack:** FastAPI + LangGraph + Qdrant + BGE-M3 + Unstructured（可选）

## Global Constraints

- Python 3.12：f-string 和 docstring 中禁止中文全角标点（：，。等）
- 依赖安装统一用 `backend/.venv/bin/python3 -m pip install`
- 新增 Qdrant 集合名称统一 `documents`
- 所有 prompt 放在 `prompts/` 目录，通过 `rag/prompt_loader.py` 加载
- 所有文件工具用 UNC 路径：`//wsl.localhost/Ubuntu/home/user/...`

---

### Task 1: 扩展 intent_recognition.prompt 支持 ask_document

**Files:**
- Modify: `prompts/intent_recognition.prompt`

**Interfaces:**
- Produces: `load_prompt("intent_recognition", ...)` 现在能识别 `ask_document`

- [ ] **Step 1: 修改 intent_recognition.prompt**

```prompt
根据用户输入判断意图（只返回一个词）：
- find_similar: 用户上传了图片，想找同款或相似商品
- ask_product: 用户询问商品详情、价格、评价等
- ask_document: 用户问的是产品手册、技术规范、FAQ、文档知识（如"质保多久"、"使用说明"）
- compare: 用户要求对比多个商品
- unclear: 无法确定

用户输入：{user_input}
会话历史条数：{history_count}
```

- [ ] **Step 2: 验证导入正常**

Run: `backend/.venv/bin/python3 -c "from rag.prompt_loader import load_prompt; p=load_prompt('intent_recognition', user_input='test', history_count=0); print('OK')"`

Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add prompts/intent_recognition.prompt
git commit -m "feat: intent_recognition 支持 ask_document 意图"
```

---

### Task 2: 新建 docs_generate.prompt

**Files:**
- Create: `prompts/docs_generate.prompt`

**Interfaces:**
- Produces: `load_prompt("docs_generate", chunks_text=..., text=...)` 返回文档问答 prompt

- [ ] **Step 1: 创建文件**

```prompt
你是产品文档专家。
你的回答必须基于下面的文档片段，每条结论后用 [来源：<文件名> 第N页] 标注。

文档片段：
{chunks_text}

用户问题：{text}

输出要求：
1. 综合多个片段回答
2. 每条结论后标注来源页码
3. 信息不足时说"未在文档中找到"
4. 不要编造未在文档中的内容
```

- [ ] **Step 2: 提交**

```bash
git add prompts/docs_generate.prompt
git commit -m "feat: 新建文档问答专用 prompt 模板"
```

---

### Task 3: 扩展 Qdrant 初始化支持 documents 集合

**Files:**
- Modify: `rag/db_client.py`

**Interfaces:**
- Produces: `init_db()` 现在创建 `documents` 集合（1024d + HNSW + Payload 索引）

- [ ] **Step 1: 在 QdrantManager.init_collection 中添加 documents 分支**

```python
def init_collection(self, collection_name="products"):
    ...
    if collection_name == "documents":
        vectors_config = {
            "text": VectorParams(size=1024, distance=Distance.COSINE),
        }
```

- [ ] **Step 2: 在 init_db() 中添加 init_collection("documents")**

```python
def init_db():
    manager = QdrantManager()
    manager.init_collection("products")
    manager.init_collection("citations")
    manager.init_collection("documents")  # 新增
    # Payload 索引
    for col in ["products", "documents"]:
        try:
            manager._create_payload_indexes(col)
        except Exception as e:
            logger.warning(f"补建 {col} 索引失败: {e}")
    # citations 的 product_id 索引
    try:
        manager.client.create_payload_index(
            collection_name="citations",
            field_name="product_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
    except Exception as e:
        if "already exists" not in str(e).lower():
            logger.warning(f"补建 citations 索引失败: {e}")
```

- [ ] **Step 3: 验证导入**

Run: `backend/.venv/bin/python3 -c "from rag.db_client import init_db, QdrantManager; print('OK')"`

Expected: `OK`

- [ ] **Step 4: 提交**

```bash
git add rag/db_client.py
git commit -m "feat: Qdrant 新增 documents 集合初始化"
```

---

### Task 4: 实现 DocLoader 文档加载器

**Files:**
- Create: `rag/doc_loader.py`
- Test: `python3 -c` 内联验证

**Interfaces:**
- Produces: `DocLoader`, `DocChunk` — `load(file_path) -> List[DocChunk]`, `load_directory(dir_path) -> List[DocChunk]`

- [ ] **Step 1: 创建 DocChunk 数据类 + DocLoader 类**

```python
"""统一文档加载器：自动检测格式，支持 PDF/MD/TXT/XLSX/DOCX"""

import os
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DocChunk:
    content: str
    chunk_type: str = "paragraph"  # title | paragraph | table | list_item
    source_file: str = ""
    page_number: Optional[int] = None
    doc_id: str = ""
    metadata: dict = field(default_factory=dict)


class DocLoader:
    """文档加载器：自动检测格式 -> 解析 -> 清洗 -> 分块"""

    SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".csv", ".xlsx", ".docx"}

    def load(self, file_path: str) -> list[DocChunk]:
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            logger.warning("不支持的格式: %s，跳过", ext)
            return []

        doc_id = f"doc_{os.path.basename(file_path).split('.')[0]}"

        # 按格式解析
        if ext == ".md":
            raw = self._parse_markdown(file_path, doc_id)
        elif ext == ".txt":
            raw = self._parse_text(file_path, doc_id)
        elif ext == ".csv":
            raw = self._parse_csv(file_path, doc_id)
        elif ext == ".pdf":
            raw = self._parse_pdf(file_path, doc_id)
        elif ext == ".xlsx":
            raw = self._parse_xlsx(file_path, doc_id)
        elif ext == ".docx":
            raw = self._parse_docx(file_path, doc_id)
        else:
            return []

        # 清洗
        cleaned = self._clean(raw)
        return cleaned

    def load_directory(self, dir_path: str) -> list[DocChunk]:
        all_chunks = []
        for fname in sorted(os.listdir(dir_path)):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in self.SUPPORTED_EXTENSIONS:
                continue
            fpath = os.path.join(dir_path, fname)
            try:
                chunks = self.load(fpath)
                all_chunks.extend(chunks)
                logger.info("加载 %s: %d chunks", fname, len(chunks))
            except Exception as e:
                logger.error("加载 %s 失败: %s", fname, e)
        return all_chunks

    def _clean(self, chunks: list[DocChunk]) -> list[DocChunk]:
        seen_hashes = set()
        result = []
        for c in chunks:
            text = c.content.strip()
            if len(text) < 10:
                continue  # 去短
            h = hashlib.md5(text[:100].encode()).hexdigest()
            if h in seen_hashes:
                continue  # 去重
            seen_hashes.add(h)
            c.content = text
            result.append(c)
        return result

    def _chunk(self, text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
        if len(text) <= chunk_size:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(text[start:end])
            start += chunk_size - overlap
        return chunks

    def _parse_markdown(self, file_path: str, doc_id: str) -> list[DocChunk]:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        chunks = []
        for i, block in enumerate(text.split("\n\n")):
            block = block.strip()
            if not block:
                continue
            ct = "title" if block.startswith("#") else "paragraph"
            for sub in self._chunk(block):
                chunks.append(DocChunk(
                    content=sub, chunk_type=ct,
                    source_file=file_path, page_number=0,
                    doc_id=f"{doc_id}_{i}",
                ))
        return chunks

    def _parse_text(self, file_path: str, doc_id: str) -> list[DocChunk]:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        chunks = []
        for i, sub in enumerate(self._chunk(text)):
            chunks.append(DocChunk(
                content=sub, chunk_type="paragraph",
                source_file=file_path, page_number=0,
                doc_id=f"{doc_id}_{i}",
            ))
        return chunks

    def _parse_csv(self, file_path: str, doc_id: str) -> list[DocChunk]:
        import csv
        chunks = []
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                text = " | ".join(f"{k}: {v}" for k, v in row.items() if v)
                if text:
                    chunks.append(DocChunk(
                        content=text, chunk_type="table",
                        source_file=file_path, page_number=0,
                        doc_id=f"{doc_id}_{i}",
                    ))
        return chunks

    def _parse_pdf(self, file_path: str, doc_id: str) -> list[DocChunk]:
        try:
            import pypdf
        except ImportError:
            logger.warning("pypdf 未安装，PDF 解析将返回空。安装: pip install pypdf")
            return []
        chunks = []
        with open(file_path, "rb") as f:
            reader = pypdf.PdfReader(f)
            for page_num, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if not text.strip():
                    continue
                for i, sub in enumerate(self._chunk(text)):
                    chunks.append(DocChunk(
                        content=sub, chunk_type="paragraph",
                        source_file=file_path, page_number=page_num,
                        doc_id=f"{doc_id}_p{page_num}_{i}",
                    ))
        return chunks

    def _parse_xlsx(self, file_path: str, doc_id: str) -> list[DocChunk]:
        try:
            import openpyxl
        except ImportError:
            logger.warning("openpyxl 未安装，xlsx 解析将返回空。安装: pip install openpyxl")
            return []
        chunks = []
        wb = openpyxl.load_workbook(file_path, read_only=True)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_text = []
            for row in ws.iter_rows(values_only=True):
                vals = [str(v) for v in row if v is not None]
                if vals:
                    rows_text.append(" | ".join(vals))
            if rows_text:
                full = "\n".join(rows_text)
                for i, sub in enumerate(self._chunk(full)):
                    chunks.append(DocChunk(
                        content=sub, chunk_type="table",
                        source_file=file_path, page_number=0,
                        doc_id=f"{doc_id}_{sheet_name}_{i}",
                    ))
        return chunks

    def _parse_docx(self, file_path: str, doc_id: str) -> list[DocChunk]:
        try:
            import docx
        except ImportError:
            logger.warning("python-docx 未安装，docx 解析将返回空。安装: pip install python-docx")
            return []
        doc = docx.Document(file_path)
        full_text = "\n".join(p.text for p in doc.paragraphs)
        chunks = []
        for i, sub in enumerate(self._chunk(full_text)):
            chunks.append(DocChunk(
                content=sub, chunk_type="paragraph",
                source_file=file_path, page_number=0,
                doc_id=f"{doc_id}_{i}",
            ))
        return chunks
```

- [ ] **Step 2: 验证导入**

Run: `backend/.venv/bin/python3 -c "from rag.doc_loader import DocLoader, DocChunk; print('OK')"`

Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add rag/doc_loader.py
git commit -m "feat: DocLoader 统一文档加载器（PDF/MD/TXT/XLSX/DOCX）"
```

---

### Task 5: 创建文档入库脚本

**Files:**
- Create: `backend/scripts/ingest_documents.py`

- [ ] **Step 1: 创建脚本**

```python
"""一键将 docs/ 目录中的文档入库到 Qdrant documents 集合"""

import asyncio
import json
import logging
import os
import sys

# 确保 backend 在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rag.embedding import embed_text
from rag.db_client import get_qdrant_client
from rag.doc_loader import DocLoader
from qdrant_client.models import PointStruct

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "docs")
COLLECTION = "documents"


async def main():
    loader = DocLoader()
    docs_dir = os.path.abspath(DOCS_DIR)
    if not os.path.isdir(docs_dir):
        logger.error("目录不存在: %s", docs_dir)
        return

    chunks = loader.load_directory(docs_dir)
    if not chunks:
        logger.warning("没有找到文档")
        return

    logger.info("共加载 %d 个 chunks，开始向量化...", len(chunks))

    client = get_qdrant_client()
    points = []
    for i, chunk in enumerate(chunks):
        embedding = await embed_text(chunk.content)
        point = PointStruct(
            id=i,
            vector={"text": embedding},
            payload={
                "doc_id": chunk.doc_id,
                "chunk_id": f"{chunk.doc_id}_{i}",
                "text": chunk.content,
                "source": chunk.source_file,
                "file_name": os.path.basename(chunk.source_file),
                "page": chunk.page_number or 0,
                "chunk_type": chunk.chunk_type,
                "tenant_id": "default",
            },
        )
        points.append(point)

        if len(points) >= 50:
            client.upsert(collection_name=COLLECTION, points=points)
            logger.info("已入库 %d/%d", i + 1, len(chunks))
            points = []

    if points:
        client.upsert(collection_name=COLLECTION, points=points)

    logger.info("入库完成，共 %d 个 chunks", len(chunks))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 验证语法**

Run: `backend/.venv/bin/python3 -m py_compile backend/scripts/ingest_documents.py`

Expected: 无报错

- [ ] **Step 3: 提交**

```bash
git add backend/scripts/ingest_documents.py
git commit -m "feat: 文档入库脚本 ingest_documents.py"
```

---

### Task 6: 新增 DocumentRetriever

**Files:**
- Modify: `rag/retrievers.py`

**Interfaces:**
- Produces: `DocumentRetriever(BaseRetriever).retrieve(query, text_embedding, top_k, filters, tenant_id) -> list[dict]`

- [ ] **Step 1: 在 retrievers.py 末尾添加 DocumentRetriever**

```python
class DocumentRetriever(BaseRetriever):
    """文档检索：BGE-M3 1024d → Qdrant documents 集合"""

    async def retrieve(
        self, query="", text_embedding=None, top_k=5,
        filters=None, tenant_id="default", **kw
    ) -> list[dict]:
        from rag.text_retrieval import search_by_text
        if not text_embedding:
            return []
        # 复用 search_by_text 逻辑，查 documents 集合
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, lambda: search_by_text(
                text_embedding, top_k=top_k,
                score_threshold=0.0, tenant_id=tenant_id,
                collection_name="documents",
            )
        )
        return [
            {
                "chunk_id": r.chunk_id if hasattr(r, "chunk_id") else r.product_id,
                "text": r.description or r.name or "",
                "score": float(r.score),
                "source": getattr(r, "source", ""),
                "file_name": getattr(r, "file_name", ""),
                "page": getattr(r, "page", 0),
            }
            for r in results
        ]
```

- [ ] **Step 2: 验证导入**

Run: `backend/.venv/bin/python3 -c "from rag.retrievers import DocumentRetriever; dr=DocumentRetriever(); print('OK')"`

Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add rag/retrievers.py
git commit -m "feat: 新增 DocumentRetriever 文档检索器"
```

---

### Task 7: 扩展 text_retrieval.py 支持指定 collection_name

**Files:**
- Modify: `rag/text_retrieval.py`

**Interfaces:**
- Produces: `search_by_text(text_emb, ..., collection_name="documents")` 参数

- [ ] **Step 1: 找到 search_by_text 函数定义，添加 collection_name 参数**

```python
def search_by_text(
    text_embedding: list[float],
    top_k: int = 10,
    score_threshold: float = 0.0,
    category_filter: Optional[str] = None,
    tenant_id: str = "default",
    collection_name: str = "products",  # 新增参数
) -> list[SearchResult]:
```

在函数内部，所有 `collection_name="products"` 硬编码的地方改为 `collection_name` 参数。

- [ ] **Step 2: 验证导入**

Run: `backend/.venv/bin/python3 -c "from rag.text_retrieval import search_by_text; print('OK')"`

Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add rag/text_retrieval.py
git commit -m "feat: search_by_text 支持指定 collection_name 参数"
```

---

### Task 8: 新增 docs_search_node + docs_retrieve_node

**Files:**
- Modify: `backend/app/graph/nodes.py`

**Interfaces:**
- Consumes: `DocumentRetriever.retrieve()`, `build_default_pipeline()`, `build_clarify_gate()`
- Produces: `docs_search_node(state) -> state`, `docs_retrieve_node(state) -> state`

- [ ] **Step 1: 在 nodes.py 末尾添加两个新节点函数**

```python
async def docs_search_node(state: AgentState) -> AgentState:
    """文档检索 Node：DocumentRetriever → 后处理器管线。"""
    from rag.retrievers import DocumentRetriever
    from rag.postprocessors import PostprocessorPipeline, DynamicTopKPostprocessor, ConfidenceGatePostprocessor

    text_emb = state.get("text_embedding")
    query = state.get("rewritten_query") or state.get("text", "") or ""
    if not text_emb:
        state["docs_chunks"] = []
        return state

    retriever = DocumentRetriever()
    chunks = await retriever.retrieve(
        query=query, text_embedding=text_emb, top_k=5,
        tenant_id=state.get("tenant_id", "default"),
    )

    # 后处理器：动态 TopK + 置信度门控
    pipeline = PostprocessorPipeline()
    pipeline.add(DynamicTopKPostprocessor(min_keep=2))
    gate = ConfidenceGatePostprocessor(threshold=0.55)
    pipeline.add(gate)

    chunks = pipeline.run(chunks, state)
    state["docs_chunks"] = chunks
    state["active_chain"] = "docs"
    logger.info("[DocsSearch] Found %d chunks", len(chunks))
    return state


async def docs_retrieve_node(state: AgentState) -> AgentState:
    """文档检索 Node：获取完整段落供 LLM 引用。"""
    chunks = state.get("docs_chunks", [])
    # 已经由 DocumentRetriever 返回了完整文本，直接透传
    # 如果以后需要从 Qdrant 拉取完整文档，在这里扩展
    state["citations"] = [
        {
            "sku": c.get("chunk_id", ""),
            "snippet": c.get("text", ""),
            "source": f"{c.get('file_name', '')} 第{c.get('page', 0)}页",
        }
        for c in chunks[:5]
    ]
    state["active_chain"] = "docs"
    return state
```

- [ ] **Step 2: 检查 nodes.py 顶部导入——确保有 asyncio 可用（已有）**

- [ ] **Step 3: 验证编译**

Run: `backend/.venv/bin/python3 -m py_compile backend/app/graph/nodes.py`

Expected: 无报错

- [ ] **Step 4: 提交**

```bash
git add backend/app/graph/nodes.py
git commit -m "feat: 新增 docs_search_node + docs_retrieve_node 文档问答节点"
```

---

### Task 9: 扩展 graph.py 注册新节点 + 路由

**Files:**
- Modify: `backend/app/graph/graph.py`

- [ ] **Step 1: 在 import 中添加新节点**

```python
from app.graph.nodes import (
    ...
    docs_search_node,
    docs_retrieve_node,
)
```

- [ ] **Step 2: 在 builder.add_node 中注册**

```python
builder.add_node("docs_search", docs_search_node)
builder.add_node("docs_retrieve", docs_retrieve_node)
```

- [ ] **Step 3: 在 plan 路由中添加文档路径**

修改 `intent_recognition_node` 或 `plan_node` 使其能路由到文档路径。具体在 `plan_node` 中添加：

```python
if state.get("intent") == "ask_document":
    steps = ["embed_text", "docs_search", "docs_retrieve", "generate"]
    state["plan"] = steps
    state["plan_step"] = 0
    state["active_chain"] = "docs"
    return state
```

- [ ] **Step 4: 添加 docs_search → docs_retrieve → generate 边**

```python
builder.add_edge("docs_search", "docs_retrieve")
builder.add_edge("docs_retrieve", "generate")
```

- [ ] **Step 5: 验证编译**

Run: `backend/.venv/bin/python3 -m py_compile backend/app/graph/graph.py`

Expected: 无报错

- [ ] **Step 6: 提交**

```bash
git add backend/app/graph/graph.py
git commit -m "feat: graph.py 注册文档问答节点 + 路由"
```

---

### Task 10: 创建评测数据集

**Files:**
- Create: `rag/eval/datasets/docs_eval_dataset.jsonl`

- [ ] **Step 1: 创建 10 条文档问答测试样本**

```jsonl
{"question": "TD01 球拍质保多久", "answer": "根据产品手册，TD01 球拍质保期为一年", "expected_doc": "TD01_Manual.pdf"}
{"question": "怎么清洗羽毛球鞋", "answer": "建议用软毛刷蘸清水轻轻刷洗鞋面，避免机洗和暴晒", "expected_doc": "保养指南.md"}
{"question": "李宁羽毛球鞋尺码偏大还是偏小", "answer": "李宁羽毛球鞋尺码偏大半码，建议比平时小半码选购", "expected_doc": "尺码表.xlsx"}
...
```

创建至少 10 条，覆盖每个意图类型。

- [ ] **Step 2: 提交**

```bash
git add rag/eval/datasets/docs_eval_dataset.jsonl
git commit -m "test: 文档问答评测数据集 10 条"
```

---

### Task 11: 端到端验证

- [ ] **Step 1: 重启后端**

```bash
cd /home/user/projects/AgentProject/Agent
bash scripts/start_backend.sh
```

- [ ] **Step 2: 测试意图路由**

```bash
curl -s -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess_docs_test","text":"TD01 球拍质保多久","image_id":""}'
```

检查日志：`[Intent] Recognized: ask_document`

- [ ] **Step 3: 测试商品链路无回归**

```bash
curl -s -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess_product_test","text":"推荐一款宽脚穿的羽毛球鞋","image_id":""}'
```

检查日志：`[Intent] Recognized: ask_product` + pipeline_step 事件

- [ ] **Step 4: 验证 frontend 热更新**

Vite 自动检测到新文件变更，刷新前端即可。

- [ ] **Step 5: 提交最终状态**

```bash
git add -A
git commit -m "feat: 多 RAG 链路 MVP（商品 + 文档问答）"
```

---

## 文件变更总览

| 操作 | 文件 | 行数 |
|------|------|------|
| 修改 | `prompts/intent_recognition.prompt` | +3 |
| 新建 | `prompts/docs_generate.prompt` | ~15 |
| 修改 | `rag/db_client.py` | +15 |
| 新建 | `rag/doc_loader.py` | ~170 |
| 新建 | `backend/scripts/ingest_documents.py` | ~65 |
| 修改 | `rag/retrievers.py` | +30 |
| 修改 | `rag/text_retrieval.py` | +5 |
| 修改 | `backend/app/graph/nodes.py` | +70 |
| 修改 | `backend/app/graph/graph.py` | +15 |
| 新建 | `rag/eval/datasets/docs_eval_dataset.jsonl` | ~10 |
| **总计** | | **~400 行** |
