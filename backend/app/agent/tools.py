import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Awaitable[Any]]
    required: list[str] = field(default_factory=list)


REGISTRY: dict[str, Tool] = {}
_UPLOAD_DIR: str = ""
_RAG_STATE: dict[str, Any] = {
    "candidates": [],
    "citations": [],
}


def register_tool(
    name: str,
    description: str,
    parameters: dict,
    required: list[str] | None = None,
):
    def decorator(fn):
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters,
            required=required or [],
            fn=fn,
        )
        return fn

    return decorator


async def call_tool(name: str, **kwargs) -> str:
    tool = REGISTRY.get(name)
    if not tool:
        return json.dumps({"error": f"Tool '{name}' not found"})
    try:
        result = await tool.fn(**kwargs)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        return json.dumps({"error": str(e)})


def get_openai_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in REGISTRY.values()
    ]


def init_tools(search_results: dict, upload_dir: str):
    global _UPLOAD_DIR, _RAG_STATE
    _UPLOAD_DIR = upload_dir
    _RAG_STATE = {
        "candidates": search_results.get("candidates", []),
        "citations": search_results.get("citations", []),
    }


def get_latest_candidates() -> list[dict]:
    return list(_RAG_STATE.get("candidates", []))


def get_latest_citations() -> list[dict]:
    return list(_RAG_STATE.get("citations", []))


def _resolve_image_path(image_id: str) -> str | None:
    exact_jpg = os.path.join(_UPLOAD_DIR, f"{image_id}.jpg")
    if os.path.exists(exact_jpg):
        return exact_jpg

    direct_path = os.path.join(_UPLOAD_DIR, image_id)
    if os.path.exists(direct_path):
        return direct_path

    if os.path.isdir(_UPLOAD_DIR):
        for filename in os.listdir(_UPLOAD_DIR):
            if filename.startswith(image_id):
                return os.path.join(_UPLOAD_DIR, filename)
    return None


def _normalize_citation(item: dict) -> dict:
    sku = str(item.get("product_id") or item.get("sku") or "")
    tag = item.get("tag") or "knowledge"
    return {
        "sku": sku,
        "id": f"{sku}:{tag}",
        "snippet": item.get("content") or item.get("snippet") or "",
        "source": tag,
        "score": float(item.get("score", 0.0) or 0.0),
    }


@register_tool(
    name="search_by_image",
    description="Search similar products from the uploaded image.",
    parameters={
        "type": "object",
        "properties": {
            "image_id": {
                "type": "string",
                "description": "Image id returned by the upload API.",
            }
        },
        "required": ["image_id"],
    },
)
async def search_by_image(image_id: str) -> dict:
    logger.info(f"[Tool] search_by_image(image_id={image_id})")
    from rag.embedding import embed_image
    from rag.image_search import search_by_image as rag_search

    image_path = _resolve_image_path(image_id)
    if not image_path:
        return {"error": "image not found", "candidates": []}

    with open(image_path, "rb") as handle:
        image_bytes = handle.read()

    loop = asyncio.get_running_loop()
    embedding = await loop.run_in_executor(None, embed_image, image_bytes)
    if not embedding:
        return {"error": "image embedding failed", "candidates": []}

    results = await loop.run_in_executor(
        None,
        lambda: rag_search(embedding, top_k=5, score_threshold=0.0),
    )
    candidates = [
        {
            "sku": result.product_id,
            "score": float(result.score),
            "title": result.name,
            "image_url": result.image_url or "",
            "price": float(result.price) if result.price is not None else None,
            "description": result.description,
            "category": result.category,
            "need_clarify": bool(result.need_clarify),
        }
        for result in results
    ]
    _RAG_STATE["candidates"] = candidates
    return {
        "candidates": candidates,
        "need_clarify": bool(candidates and candidates[0].get("need_clarify")),
    }


@register_tool(
    name="get_product_detail",
    description="Get structured detail for one candidate product.",
    parameters={
        "type": "object",
        "properties": {
            "sku": {
                "type": "string",
                "description": "Candidate product sku id.",
            }
        },
        "required": ["sku"],
    },
)
async def get_product_detail(sku: str) -> dict:
    logger.info(f"[Tool] get_product_detail(sku={sku})")
    for candidate in _RAG_STATE.get("candidates", []):
        if candidate.get("sku") == sku:
            return candidate
    return {"error": f"SKU {sku} not found"}


@register_tool(
    name="search_knowledge",
    description="Retrieve knowledge snippets for the selected candidate products.",
    parameters={
        "type": "object",
        "properties": {
            "sku_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Candidate product sku ids.",
            },
            "query_text": {
                "type": "string",
                "description": "User query used for citation retrieval.",
            },
        },
        "required": ["sku_ids"],
    },
)
async def search_knowledge(sku_ids: list[str], query_text: str = "") -> dict:
    logger.info(
        f"[Tool] search_knowledge(sku_ids={sku_ids}, query_text={query_text!r})"
    )
    from rag.embedding import embed_text
    from rag.text_retrieval import get_citations, get_citations_by_sku

    citations: list[dict] = []
    loop = asyncio.get_running_loop()

    if query_text.strip():
        query_embedding = await loop.run_in_executor(None, embed_text, query_text.strip())
        if query_embedding:
            citations = await loop.run_in_executor(
                None,
                lambda: get_citations(query_embedding, sku_ids, top_k=5, query=query_text),
            )

    if not citations:
        aggregated: list[dict] = []
        for sku_id in sku_ids[:3]:
            sku_citations = await loop.run_in_executor(None, get_citations_by_sku, sku_id)
            aggregated.extend(sku_citations)
        citations = aggregated[:5]

    normalized = [_normalize_citation(item) for item in citations if item]
    _RAG_STATE["citations"] = normalized
    return {"citations": normalized}


@register_tool(
    name="clarify",
    description="Ask a clarification question when product information is insufficient.",
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Clarification question for the user.",
            }
        },
        "required": ["question"],
    },
)
async def clarify(question: str) -> dict:
    logger.info(f"[Tool] clarify(question={question})")
    return {"need_clarify": True, "clarify_question": question}


@register_tool(
    name="final_answer",
    description="Return the final answer and finish the reasoning loop.",
    parameters={
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Final user-facing answer.",
            }
        },
        "required": ["answer"],
    },
)
async def final_answer(answer: str) -> dict:
    logger.info("[Tool] final_answer()")
    return {
        "answer": answer,
        "need_clarify": False,
        "citations": get_latest_citations(),
    }
