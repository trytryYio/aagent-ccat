# 多 RAG 链路架构：商品推荐 + 文档问答 设计文档

> 日期：2026-07-05
> 状态：设计中
> 范围：将现有商品推荐链路作为 RAG chain 之一，新增通用文档问答链路

---

## 一、目标与背景

### 业务目标
当前系统是单链路 LangGraph（13 个节点，专注商品推荐）。将其重构为**多 RAG 链路架构**：
- **现有**：`product_rag` 链路（基于 Qdrant products + citations 集合）
- **新增**：`docs_rag` 链路（基于 Qdrant 新增 documents 集合）

两条链路**共享基础设施**，通过顶层意图路由器自动分发。

### 为什么不引入新依赖
- `pypdf` / `python-docx` / `openpyxl` 用 Python 标准库或最小化依赖
- 复用已搭好的：`BaseRetriever` / `PostprocessorPipeline` / `Prompt 外置` / `pipeline_step` SSE
- 避免再次大规模重构

---

## 二、整体架构

```
                       [用户输入]
                           ↓
              ┌─── intent_recognition ───┐
              │  ask_product               │
              │  find_similar             │
              │  ask_document  ← 新增     │
              │  compare                  │
              └────────────┬──────────────┘
                           ↓
                       [plan_node]
                  决定走 product / docs
                           ↓
                  ┌────────router──────────┐
                  ↓                         ↓
           [product_pipeline]        [docs_pipeline]
              embed_image(opt)         embed_text
              slot_filling             docs_search
              embed_text               docs_retrieve
              search                   │
              product_pipeline_run     │
                  ↓                    ↓
                  ├──── generate ──────┤
                  ├──── reflection
                  └──── finalize
```

### 关键决策
1. **路由点单一**：所有路由在 `intent_recognition` 处一次性完成
2. **共享 finalize**：两条链路最终汇聚到 `generate` → `reflection` → `finalize`
3. **state["active_chain"]** 字段记录当前激活链路，便于 pipeline_step 前端展示
4. **同一聊天窗口**：前端不区分，按 pipeline_step 事件自然过渡

---

## 三、数据流与组件

### 3.1 Qdrant 集合扩展

| 集合 | 维度 | 字段 | 用途 | 状态 |
|------|------|------|------|------|
| `products` | 1024+512 | sku/name/desc/category | 商品 | 已有 |
| `citations` | 1024 | sku_id/chunk/snippet | 商品知识片段 | 已有 |
| **`documents`** | 1024 | doc_id/chunk_id/text/page/source/file_name | 通用文档 | **新增** |

`documents` 集合 Payload 设计：
```python
{
    "doc_id": "doc_td01_manual",       # 文档唯一 ID
    "chunk_id": "doc_td01_001",         # chunk 唯一 ID（doc_id + 下标）
    "source": "/docs/TD01_Manual.pdf",  # 原始文件路径
    "file_name": "TD01_Manual.pdf",     # 文件名（用于引用展示）
    "page": 3,                          # PDF 页码，其他格式为 0
    "chunk_index": 1,                   # 在文档中的序号
    "chunk_type": "paragraph",          # paragraph / table / title
    "tenant_id": "default",             # 多租户隔离
}
```

### 3.2 文档入库流程（一次性脚本）

```
docs/
├── TD01_Manual.pdf          ← 用户放入
├── FAQ_通用.md
├── 尺码表.xlsx
└── 售后说明.docx
   ↓ DocLoader.load()       [自动检测格式]
   ↓ _clean()               [去空 / 去短 / 去重（前 100 字符 hash）]
   ↓ _chunk(512, 50)        [按字符分块，overlap 50]
   ↓ embed_text(BGE-M3)
   ↓ upsert(documents 集合)
```

### 3.3 单次问答数据流（用户问"TD01 球拍质保多久"时）

1. `intent_recognition` → ask_document
2. `plan` → 第一步：embed_text（无需 rewrite/slot_filling）
3. `docs_search_node` → `DocumentRetriever.retrieve()` → top-5 文档片段
4. `docs_retrieve_node` → 整段文档拉取 + Rerank → 给 LLM 完整上下文
5. `generate_node` 使用 `load_prompt("docs_generate", chunks=..., text=...)`
6. `reflection_node` 复用 — 自检是否有引用、是否编造
7. `finalize_node` 复用

---

## 四、组件清单

### 4.1 新增文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `rag/doc_loader.py` | ~120 | DocLoader 类：load / _clean / _chunk / `DocChunk` 数据类 |
| `rag/retrievers.py` (扩展) | +30 | `DocumentRetriever(BaseRetriever)` |
| `prompts/docs_generate.prompt` | ~30 | 文档问答专用 prompt（含引用标注规则） |
| `prompts/intent_recognition.prompt` (扩展) | +10 | 新增 `ask_document` 类别 |
| `backend/scripts/ingest_documents.py` | ~80 | 一键入库脚本 |
| `backend/app/graph/nodes.py` (扩展) | +120 | 新增 `docs_search_node` / `docs_retrieve_node`，扩展 `plan_node` |
| `backend/app/graph/graph.py` (扩展) | +25 行 | 注册新节点 + 路由 + `state["active_chain"]` 字段初始化 |
| `backend/app/core/stream_manager.py` | 不变 | 复用 pipeline_step 事件 |
| **总计** | **~410 行** | 增量改动 |

### 4.0 state["active_chain"] 字段

AgentState 新增 `active_chain: str` 字段，取值 `"product"` / `"docs"` / `"unknown"`。

- `intent_recognition_node` 在识别 `ask_document` 时设置 `"docs"`，其他情况 `"product"`
- `pipeline_step` SSE 事件携带 `chain` 字段给前端，前端可选择性展示（如"文档检索中..."标签）
- `plan_node` 根据 `active_chain` 决定第一步路径

### 4.6 跨链路上下文

为避免同会话内"刚才问球拍现在问鞋"的混淆：

- `last_chain` 字段保存上一轮 `active_chain`
- 当本轮 LLM 意图识别置信度低（top1 < 0.5）时，倾向沿用 `last_chain`
- `reflection_node` 检查是否出现"答非所问"（链路口径不一致）

### 4.2 DocLoader 类接口

```python
@dataclass
class DocChunk:
    content: str
    chunk_type: str      # "title" | "paragraph" | "table" | "list_item"
    source_file: str
    page_number: Optional[int]
    doc_id: str
    metadata: dict

class DocLoader:
    def load(self, file_path: str) -> List[DocChunk]:
        """自动检测格式（PDF/MD/XLSX/DOCX）→ 解析 → 清洗 → 分块"""

    def load_directory(self, dir_path: str) -> List[DocChunk]:
        """批量加载目录下所有支持的文档"""

    def _clean(self, chunks: List[DocChunk]) -> List[DocChunk]:
        """去空 / 去短 (<10字) / 去重（前 100 字符 hash）"""

    def _chunk(self, content: str, chunk_size: int = 512, overlap: int = 50) -> List[str]:
        """按字符分块，避免切断句子"""

    def _detect_format(self, file_path: str) -> str:
        """根据扩展名自动选择解析器"""
```

### 4.3 DocumentRetriever 类

```python
class DocumentRetriever(BaseRetriever):
    """从 documents 集合检索，返回 [{doc_id, chunk_id, text, page, score, file_name}]"""

    async def retrieve(
        self,
        query: str = "",
        text_embedding: Optional[list[float]] = None,
        top_k: int = 5,
        filters: Optional[dict] = None,
        tenant_id: str = "default",
    ) -> list[dict]:
        # 复用 search_by_text_tool 模式
        # 应用 PostprocessorPipeline：Rerank → ConfidenceGate → DynamicTopK
```

### 4.4 文档问答 Prompt（docs_generate.prompt）

```
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

### 4.5 Intent Recognition Prompt 扩展

```
新增类别：
- ask_document: 用户问的是产品手册、技术规范、FAQ、文档知识（如"质保多久"、"使用说明"）
- find_similar: （已有）用户上传图片找同款
- ask_product: （已有）问商品详情、价格、评价
- compare: （已有）多商品/多文档对比
- unclear: 无法确定
```

---

## 五、错误处理

| 错误类型 | 处理策略 |
|---------|---------|
| 文档解析失败（单文件） | 不阻塞整体入库，记录 `ingest_errors.log`，前端展示哪些文档失败 |
| PDF 加密/损坏 | 解析器抛异常 → DocLoader 跳过并记录 |
| 文档检索无结果 | 触发 ConfidenceGate（阈值 0.55），友好询问 |
| 文档超长（>50 页） | 递归分块，单块上限 1024 tokens |
| 跨链路冲突 | 同会话内记录 `last_chain`，意图模糊时倾向上一轮链路 |
| 多租户隔离 | documents 集合的 `tenant_id` Payload 过滤 |

---

## 六、测试与验收

### 6.1 功能验收
- [ ] 成功入库 5 份混合格式测试文档（PDF+MD+XLSX+DOCX）
- [ ] `ask_document` 意图识别准确率 > 85%（用 20 条标注样本测试）
- [ ] 文档问答 RAGAS Faithfulness > 0.7
- [ ] 同聊天窗口内：
  - 问"推荐一款宽脚穿的鞋" → 走 product_rag
  - 问"TD01 球拍质保多久" → 走 docs_rag
  - 问"对比 X 和 Y 球拍" → 走 product_rag 或 multi

### 6.2 回归验收
- [ ] 商品链路（原有 13 nodes）零回归
- [ ] pipeline_step SSE 事件正常推送
- [ ] 多租户隔离未破坏
- [ ] ResilientLLM fallback 正常

### 6.3 测试数据集
- 创建 `rag/eval/datasets/docs_eval_dataset.jsonl`（10 条文档问答样本）
- 创建 `rag/eval/datasets/routing_eval_dataset.jsonl`（20 条意图路由样本）

---

## 七、实施分阶段

### Phase 1：基础设施 (Day 1)
- `rag/doc_loader.py` — Unstructured 集成、DocChunk、load/clean/chunk
- `rag/db_client.py` — 扩展 `init_collection("documents")` + HNSW + Payload 索引
- `backend/scripts/ingest_documents.py` — 一键入库脚本

### Phase 2：检索层 (Day 2)
- `rag/retrievers.py` 新增 `DocumentRetriever`
- `prompts/docs_generate.prompt` 新建
- `prompts/intent_recognition.prompt` 扩展支持 `ask_document`

### Phase 3：链路层 (Day 3)
- `backend/app/graph/nodes.py` 新增 `docs_search_node`、`docs_retrieve_node`
- `backend/app/graph/graph.py` 注册新节点 + 路由
- `intent_recognition_node` 扩展
- `plan_node` 扩展

### Phase 4：测试与文档 (Day 4)
- 5-10 个文档问答样本做 RAGAS 评测
- 写 `docs/rag/docs_rag_pipeline.md`
- 写 `docs/progress/2026-07-XX-multi-rag-design.md`

---

## 八、风险与缓解

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| Unstructured 安装复杂（依赖多） | 阻塞 Phase 1 | 用最小子集 `unstructured[pdf,md,xlsx,docx]`，逐个启用 |
| 意图识别准确率不够 | 路由错误 | ConfidenceGate 兜底 + 用户反馈循环 |
| 文档格式千奇百怪 | 解析失败多 | 提供 ingest_errors.log + 手动标注清单 |
| 商品/文档意图混淆 | 体验下降 | 上下文强化（last_chain 偏好） |

---

## 九、YAGNI 排除清单（暂不做）

- ❌ 文档版本管理
- ❌ 文档协作编辑
- ❌ 文档权限分级（仅 tenant_id 隔离）
- ❌ 多语言文档
- ❌ OCR 扫描件
- ❌ 知识图谱（KG）
- ❌ HyDE 检索（文档型无需，商品型已评估 ROI 低）
- ❌ 多文档对比 intent（unclear 处理）

---

## 十、参考

- 现有方案文档：`docs/optimization/PythonProject16对比分析.md`
- 当前架构：`backend/app/graph/graph.py`
- 已有 retriever 抽象：`rag/retrievers.py`
- 后处理器管线：`rag/postprocessors.py`
- Prompt 外置：`prompts/*.prompt` + `rag/prompt_loader.py`