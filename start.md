# 启动指南 (start.md)

李宁羽毛球鞋多模态 RAG 导购 Agent —— 基于 **LangGraph StateGraph + 四层记忆 + CLIP/BGE-M3 + RAGAS + GSAP**。

---

## 0. 架构总览

```
用户(文本/图片) → Vue3 前端 → FastAPI(/api/v1/chat, SSE)
                                   ↓
                      LangGraph StateGraph (12 节点)
   intent_recognition → plan → embed_image/embed_text → search
        → decide_clarify →〔澄清〕ask_clarify
                         →〔继续〕retrieve_citations → generate(真流式) → reflection → finalize
                                   ↓
            Qdrant Cloud (products: text+image 双向量, citations: 知识片段)
            DeepSeek LLM (OpenAI 兼容协议)
```

四层记忆：工作记忆(AgentState) / 情景记忆(Session 内存) / 语义记忆(偏好) / 感知记忆(CLIP+BGE-M3 向量)。

---

## 1. 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 推荐 3.12 |
| Node.js | 18+ | 前端构建 |
| 网络 | — | HuggingFace 镜像(hf-mirror.com)、DeepSeek API、Qdrant Cloud |

磁盘：AI 模型缓存约 **2.8GB**（CLIP 600MB + BGE-M3 2.2GB），首次下载。

---

## 2. 首次部署

### 2.1 后端 Python 环境

```bash
cd Agent/backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# requirements.txt 未列 langgraph，单独装：
pip install langgraph
```

### 2.2 配置环境变量

```bash
cp .env.example .env
```

编辑 `backend/.env`，**必填**以下项：

```ini
# LLM (DeepSeek，OpenAI 兼容协议)
LLM_API_KEY=sk-你的deepseek密钥
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash

# Qdrant Cloud (免费版即可，去 qdrant.tech 注册)
QDRANT_URL=https://你的集群地址.cloud.qdrant.io
QDRANT_API_KEY=你的qdrant密钥
```

**选填**（图片上传到阿里云 OSS；不填则图片只存本地）：
```ini
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
OSS_BUCKET_NAME=...
```

### 2.3 下载 AI 模型（首次，约 2.8GB）

模型在首次向量化时自动从 hf-mirror.com 下载。手动触发：

```bash
cd Agent
source backend/.venv/bin/activate
PYTHONPATH=$(pwd) python -c "from rag.embedding import get_engine; e=get_engine(); e._ensure_bge(); print('模型就绪')"
```

下载完成后，**建议设环境变量跳过联网检查**（加速启动）：
```bash
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

### 2.4 数据入库（Qdrant）

按顺序执行三条命令（都在 `Agent/` 根目录，venv 已激活）：

```bash
# 1. 下载商品图片到 rag/data/images/（picsum 占位图，CLIP 可编码）
PYTHONPATH=$(pwd) python -m rag.scripts.download_images

# 2. 商品入库：向量化(text+image) → Qdrant products 集合
export $(grep -v '^#' backend/.env | xargs)   # 加载 Qdrant 配置
PYTHONPATH=$(pwd) python -m rag.scripts.ingest_data --recreate

# 3. 知识切片入库：按特性切分描述 → Qdrant citations 集合
PYTHONPATH=$(pwd) python -m rag.scripts.refine_knowledge_base
```

预期结果：
- `products` 集合：**25 条**，每条带 `text`(1024d) + `image`(512d) 双向量
- `citations` 集合：**120 条**知识片段

> 若上传超时（`write operation timed out`），重跑即可（幂等）；已在 db_client 设 timeout=60。

### 2.5 启动后端

```bash
cd Agent/backend
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PYTHONPATH=$(pwd):$(dirname $(pwd)) python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

启动日志应出现：
```
Qdrant 集合就绪 (products + citations)
Application startup complete.
Uvicorn running on http://0.0.0.0:8000
```

健康检查：`curl http://localhost:8000/api/v1/health` → `{"status":"ok"}`

> 若看到 `LLM 探活失败（不阻断启动）` 警告可忽略，不影响实际请求。

### 2.6 启动前端

```bash
cd Agent/web
npm install
npm run dev
```

打开浏览器访问 Vite 提示的地址（通常 `http://localhost:5173`）。

> 前端默认连接 `http://localhost:8000`。如后端在别的地址，在前端「设置」页修改 Base URL（存 localStorage）。

---

## 3. 日常启动（已部署过）

每次开发只需两条命令：

```bash
# 终端 1 - 后端
cd Agent/backend && source .venv/bin/activate && \
  export $(grep -v '^#' .env | xargs) HF_HUB_OFFLINE=1 && \
  PYTHONPATH=$(pwd):$(dirname $(pwd)) python -m uvicorn app.main:app --reload --port 8000

# 终端 2 - 前端
cd Agent/web && npm run dev
```

---

## 4. 使用演示

### 文本检索（主推，效果最好）
在前端输入框发送：
- `变色龙入门训练鞋` → 命中入门款
- `雷霆80专业碳板比赛鞋` → 命中高端碳板（分数接近时触发澄清）

效果：商品卡片交错入场 → AI **逐 token 流式**回答 → 知识引用展示。

### 图片检索（多模态能力展示）
点击上传按钮，上传一张图片 → 系统用 CLIP 向量搜相似商品。

> 注：当前 `rag/data/images/` 是 picsum 占位图，以图搜图的语义相关性有限。接入李宁官网真实图床后效果更准（见 `rag/scripts/download_images.py` 文末注释的 Playwright 升级路径）。

### 多轮澄清
若系统追问「预算/用途」，回复后不再重复追问（澄清状态跨会话恢复）。

---

## 5. 质量评估（RAGAS）

```bash
cd Agent
source backend/.venv/bin/activate
PYTHONPATH=$(pwd):$(pwd)/backend python rag/eval/ragas_eval.py
```

自动生成回答 → 计算 Faithfulness / Context Recall / Context Precision，报告输出到 `rag/eval/reports/`。

---

## 6. 常见问题

| 问题 | 解决 |
|------|------|
| `ModuleNotFoundError: app` / `rag` | 后端必须从 `backend/` 目录启动，且 PYTHONPATH 含 `backend/` 和项目根 |
| AI 模型下载慢/失败 | 已配 hf-mirror.com；仍失败可手动设 `HF_ENDPOINT=https://hf-mirror.com` |
| 首次请求很慢（10-20s） | 正在加载 CLIP+BGE-M3 到内存，后续请求正常 |
| Qdrant 写入超时 | 已设 timeout=60；重跑入库脚本（幂等） |
| Qdrant Cloud 503 `no available server` | 免费版会休眠，去 Qdrant 控制台点 Resume |
| 前端看不到 AI 回答 | 确认后端 SSE 发 `delta_text`（非 `delta`）、candidates 包成 `{candidates:[]}` |
| 前端 node_modules 损坏 | `cd web && rm -rf node_modules package-lock.json && npm install` |

---

## 7. 目录结构

```
Agent/
├── backend/
│   ├── app/
│   │   ├── api/          # chat.py(SSE真流式) upload.py health.py
│   │   ├── graph/        # ★ LangGraph: state/nodes/graph/tools/llm/memory
│   │   ├── core/         # session + stream manager
│   │   ├── main.py       # FastAPI + lifespan 启动初始化
│   │   └── config.py
│   ├── .env              # ★ 必须配置
│   └── requirements.txt
├── web/                  # Vue3 + TS + GSAP + Tailwind
│   └── src/{views,components,composables,api}/
├── rag/
│   ├── embedding.py      # CLIP + BGE-M3
│   ├── {image,text,hybrid}_search.py
│   ├── db_client.py      # Qdrant
│   ├── scripts/          # download_images/ingest_data/refine_knowledge_base
│   ├── data/             # products.csv + images/
│   └── eval/             # RAGAS 评估
├── docs/teaching/        # 7 篇教学 MD + 毕设大纲
└── start.md              # 本文件
```

---

## 8. 关键技术点

- **真流式**：`agent_graph.astream_events(version="v2")` 捕获 generate 节点的 LLM token，逐字推 SSE（非 20 字符切片）
- **多 Agent 编排**：Intent Recognition → Plan & Solve → ReAct(Think-Act-Observe) → Reflection
- **四层记忆**：工作/情景/语义/感知，澄清状态跨会话恢复
- **多模态 RAG**：CLIP(512d 图) + BGE-M3(1024d 文) + Qdrant 命名向量 + RRF 融合
- **GSAP**：消息淡入 + 卡片交错入场
