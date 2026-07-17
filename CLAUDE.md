# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Global Rules

- **Language:** Always reply in Chinese (中文). All responses, explanations, and communications should be in Chinese unless the user explicitly requests otherwise.
- **Working Directory Path:** This project now runs directly on Windows. The project root is `D:\project\Agent`. Use Windows paths for all file operations and shell commands unless explicitly confirmed otherwise.
- **Debugging Reference:** When encountering repeated errors in this project, first check the pitfalls memory at `C:\Users\a1782\.claude\projects\--wsl-localhost-Ubuntu-home-user-projects-AgentProject-Agent\memory\project-pitfalls.md` for root cause and solution before trying workarounds. Note: this file was created during the WSL era; mentally translate WSL paths to `D:\project\Agent`.

## 开发护栏规则（强制执行，防止重复错误）

### 1. Edit 前必须 Read
- **禁止** 在未 Read 目标文件的情况下使用 Edit 工具
- Edit 的 `old_string` 必须从最近一次 Read 的输出中精确复制
- 大段替换（>20 行）优先用 Write 全量重写

### 2. 新增/修改 Node 后的验证清单
- [ ] `backend/app/graph/nodes.py` 中函数定义完整（两个空行 + `async def`）
- [ ] `backend/app/graph/graph.py` 的 import 列表已更新
- [ ] `builder.add_node("name", func)` 已注册
- [ ] 边（edges）已正确连接
- [ ] 用 Python 编译检查：`python -m py_compile backend/app/graph/nodes.py`

### 3. 依赖安装统一进 venv
- **禁止** 使用系统 `python -m pip install` 或 `pip install`
- **必须** 使用 `backend/.venv/Scripts/python.exe -m pip install <package>`（在 PowerShell 中先激活 venv 也可以）
- 新增依赖后更新 `backend/requirements.txt`

### 4. Windows / PowerShell 命令执行规范
- 文件工具（Read/Write/Edit/Glob/Grep）使用 Windows 路径：`D:\project\Agent\...`
- Bash 中调用 Windows 命令时直接用 `bash`（Git Bash），路径用 `/d/project/Agent/...` 这种 POSIX 形式
- 推荐用 PowerShell 启动后端：
  ```powershell
  cd D:\project\Agent\backend
  .venv\Scripts\Activate.ps1
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  ```
- 复杂 Python 测试脚本先 Write 到文件再执行，**禁止** 用 Bash heredoc 传含 f-string 的代码

### 5. 启动后端前先读 pitfalls.md
- 每次执行 Python/uvicorn 命令前，**必须**先读 `project-pitfalls.md` 的"路径与执行环境"章节
- 优先用 PowerShell 启动后端，避免端口占用和路径问题
- 启动后必须验证：`curl http://localhost:8000/api/v1/health`
- 如果 health check 失败，查看后端日志或控制台输出定位错误

### 5. 中文编码陷阱
- Python 3.12 的 f-string/docstring 中**禁止**使用中文全角标点（`：` `，` `。` 等）
- 所有 docstring 和 f-string 中使用英文半角标点

### 6. 后端启动验证
- 每次修改后端代码后，必须验证启动：`curl http://localhost:8000/api/v1/health`
- 如果 health check 失败，查看后端日志或控制台输出
- 启动命令：
  ```powershell
  cd D:\project\Agent\backend
  .venv\Scripts\Activate.ps1
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  ```

## Project Overview

**Android 拍照识图 + RAG 电商导购 Agent (PoC)** — a multi-modal shopping assistant for Li-Ning badminton shoes (~105 SKUs). Input: photo + optional text. Output: top-k similar product cards + streaming shopping guide with citations.

3-person team split: Android (Kotlin), Backend (FastAPI), RAG/Multimodal (Python).

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI, LangChain, DeepSeek API, Qdrant, Redis |
| Frontend | Vue 3, Vite, Tailwind CSS, TypeScript |
| RAG | CLIP (image, 512d), BGE-M3 (text, 1024d), Qdrant Cloud, RRF fusion |
| Android | Kotlin, Gradle |
| Infra | Docker Compose, nginx |

## Architecture

```
Agent/
├── backend/          # FastAPI backend (port 8000)
│   └── app/
│       ├── api/      # Routes: chat.py, upload.py, health.py
│       ├── agent/    # Orchestration: orchestrator.py, react_loop.py, tools.py, memory.py
│       ├── core/     # session.py, stream_manager.py
│       ├── config.py # Settings (LLM, Qdrant, Redis)
│       └── models.py # Pydantic models (Candidate, Citation, ChatRequest)
├── web/              # Vue 3 frontend (port 5173 dev / 8080 prod)
│   └── src/
│       ├── components/  # MessageBubble, CandidateCard, CitationSection, ImageUploadDialog, etc.
│       ├── api/         # client.ts, sse.ts, chat.ts
│       ├── composables/ # useChat.ts
│       └── types/       # TypeScript definitions
├── rag/              # Python RAG module (imported by backend, not a service)
│   ├── embedding.py       # CLIP + BGE-M3 model loading
│   ├── image_search.py    # Image-based Qdrant search
│   ├── text_retrieval.py  # Text search + citation retrieval
│   ├── hybrid_search.py   # RRF image+text fusion
│   ├── db_client.py       # Qdrant connection
│   ├── scripts/           # Data ingestion, scraping, eval, stress test
│   ├── data/              # products.csv, images/
│   └── eval/              # Datasets & evaluation reports
├── android/          # Kotlin Android app
└── docker-compose.yml
```

**Key data flow:** Upload image → image embedding → Qdrant top-k → candidates → text retrieval (scoped to candidates) → citations → LLM streaming response with candidates + citations.

## Key Commands

### Backend (local dev)

```powershell
cd D:\project\Agent\backend
.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend (local dev)

```powershell
cd D:\project\Agent\web
npm install    # first time only
npm run dev
```

### Docker (full stack)

```bash
docker compose up --build -d
docker compose logs -f          # tail logs
docker compose logs -f backend  # single service logs
docker compose down -v          # stop + clean volumes
docker compose up --build -d backend  # rebuild single service
```

Access at **http://localhost** (nginx proxies `/api/*` → backend, rest → frontend).

### RAG Data Pipeline

```powershell
# Set PYTHONPATH to project root before any RAG script
$env:PYTHONPATH = "D:\project\Agent"

# Ingest products.csv → Qdrant
python -m rag.scripts.pipeline.ingest_data

# Scrape Li-Ning store
python -m rag.scripts.scrapers.lining_scraper_v3 --headful --limit 50
python -m rag.scripts.scrapers.lining_scraper_v3 --limit 50
python -m rag.scripts.scrapers.lining_scraper_v3 --category 男鞋
python -m rag.scripts.scrapers.lining_scraper_v3 --skip-detail
python -m rag.scripts.scrapers.lining_scraper_v3 --skip-list

# Evaluate retrieval performance
python -m rag.scripts.eval.evaluate_performance --modes image hybrid --top-k 10

# End-to-end eval (requires backend running)
python -m rag.scripts.eval.evaluate_end_to_end --mode api --base-url http://127.0.0.1:8000

# Stress test
python -m rag.scripts.eval.stress_test
```

### RAG Import Interface

```python
from rag.embedding import embed_image, embed_text
from rag.image_search import search_by_image, SearchResult
from rag.text_retrieval import search_by_text, get_citations, get_citations_by_sku
from rag.hybrid_search import hybrid_search
```

## API Contract

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/upload/image` | POST | Upload image (multipart) → `{image_id, url}` |
| `/api/v1/chat` | POST | Create chat session → `{session_id, message_id, stream_url}` |
| `/api/v1/chat/stream?message_id=` | GET (SSE) | Stream events: `delta_text`, `candidates`, `citations`, `final` |
| `/api/v1/chat/stop` | POST | Cancel generation |
| `/api/v1/health` | GET | Health check |

## Retrieval Strategy

- **Image threshold:** `score_threshold=0.6` (default)
- **Text threshold:** `score_threshold=0.5` (default)
- **Clarify trigger:** top1 < 0.6 OR top1-top2 gap too small OR flat score distribution
- **Clarify questions (1 per round):** budget, usage scenario, key preference
- **Generation constraint:** must base output on candidate SKUs + knowledge chunks; say "uncertain" when insufficient

## Evaluation Metrics

- Top-1 hit rate, Top-3 hit rate
- Answer usefulness score
- Clarify trigger rate
- Citation consistency
- Latency P50/P95

## Git Workflow

- **Branching:** GitHub Flow with `develop` as integration branch
- **Naming:** `type/name/feature` (e.g. `feat/zhangsan/add-image-search`)
- **Commits:** Conventional Commits in Chinese (`feat: 接入 CLIP 模型实现图像特征提取`)
- **Merging:** Squash merge to `develop`; never push directly to `main` or `develop`

## 进展记录规则（重要！）

每次完成阶段性任务（如多租户架构、LLM 治理、评测优化等）后，**必须**按以下流程执行：

1. **创建进展记录文件**
   - 路径：`docs/progress/YYYY-MM-DD-任务简称.md`
   - 内容：完成清单、技术决策、Git 提交计划、面试素材更新
   - 参考：`docs/progress/2026-07-01-afternoon.md`

2. **更新长期目标**
   - 打开 `长期目标.md`
   - 找到对应对应章节的 checkbox
   - 完成的打 `[x]`，未完成的保持 `[ ]`

3. **立即执行 Git 提交**
   - 不要等到用户提醒
   - 提交信息要清晰，包含改动的文件和目的
   - 示例：`feat: 多租户架构 MVP（租户隔离 + 配额管理 + Admin API）`

4. **更新面试素材**（如适用）
   - 检查 `docs/interview/面试准备.md` 是否需要更新简历 bullet
   - 新增的技术点可以加到"高频面试问题预演"中

**为什么要这样做？**
- 避免做完就忘，方便回顾
- Git 历史清晰，面试时能展示开发过程
- 长期目标实时更新，知道自己做到哪了

## Environment

- Backend config: `backend/.env` (gitignored, copy from `.env.example`)
- LLM: DeepSeek API (configured in `.env`)
- Qdrant: Cloud by default, local Docker fallback
- Redis: Connected via `host.docker.internal` in Docker mode
- Python: Windows venv at `backend/.venv` (use `backend/.venv/Scripts/python.exe`)

## Data Files

- Products: `rag/data/products.csv` (105 SKUs)
- Images: `rag/data/images/` (33 local JPGs, rest via URL)
- Evaluation datasets: `rag/eval/datasets/rag_eval_dataset.jsonl`, `rag_e2e_dataset.jsonl`
- Evaluation reports: `rag/eval/reports/<run_id>/`

## 毕设论文方面
- 请打开 `D:\project\Agent\报告.md`
- 毕设大纲要求以及一些详细的内容在 `D:\project\Agent\docs\teaching` 中

## 项目论文辅助写作规则

- 请打开 `D:\project\Agent\l论文辅助.md`

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
