# 项目当前状态总结（2026-07-01 晚间）

> 更新日期：2026-07-01 18:30  
> 距离面试：约 1 周

---

## 一、已完成模块（可写进简历）

### ✅ 阶段一：数据链路（80% 完成）

| 任务 | 状态 | 指标 |
|------|------|------|
| 重排（rerank） | ✅ 完成 | 千问 gte-rerank-v2，Top-3 从 65% → 69% |
| 评测集 | ✅ 完成 | 29 条标注用例，4 种场景 |
| RAGAS 四维指标 | ⚠️ 部分 | Faithfulness 0.82 ✅ / Context Precision 0.82 ✅ / Context Recall 0.40 ⚠️ / Answer Relevancy 已修复待重测 |
| image 评测用例 | ❌ 未做 | 优先级低 |

**简历 bullet**：
```
· 在混合召回后引入 cross-encoder 重排（千问 gte-rerank-v2）对 query-候选对精排，
  hybrid Top-3 命中率从 65.52% 提升至 68.97%。
· 搭建 RAGAS 评测链路，Faithfulness 0.82 / Context Precision 0.82，
  建立 29 条标注用例的检索评测基准。
```

### ✅ 阶段二：工程化闭环（90% 完成）

| 任务 | 状态 | 说明 |
|------|------|------|
| Redis 会话持久化 | ✅ 完成 | SessionManager 改 Redis Hash，7 天 TTL |
| LangGraph Redis checkpointer | ✅ 完成 | 自实现 RedisSaver，thread_id=session_id |
| 四层记忆落地 | ✅ 完成 | Working/Episodic/Semantic/Perceptual |
| 长对话压缩 | ✅ 完成 | 超过 10 轮自动摘要 + 保留最近 6 轮 |
| LLM fallback | ✅ 完成 | DeepSeek → 通义千问 qwen-plus |
| 可观测（日志/探针） | ✅ 完成 | ObservabilityMiddleware + /api/v1/ready |
| Prometheus 指标 | ❌ 未做 | 优先级低 |

**简历 bullet**：
```
· 将对话会话与 Agent 状态从进程内存迁移至 Redis 持久化，
  实现后端无状态化，支持多副本水平扩展。
· 实现分层对话记忆：短期会话上下文持久化 + 长期用户偏好跨会话抽取注入，
  支持多轮连贯推荐。
· 封装 LLM 调用治理层：指数退避重试 + 熔断 + 多模型 fallback + 限流 + token 成本统计。
```

### ✅ 阶段三：多租户架构（70% 完成）

| 任务 | 状态 | 说明 |
|------|------|------|
| 租户模型 | ✅ 完成 | Tenant/TenantQuota/TenantUsage |
| X-Tenant-ID 中间件 | ✅ 完成 | 解析 + 校验 + 注入 request.state |
| Redis key 租户隔离 | ✅ 完成 | agent:tenant:{tenant_id}:session:{sid} |
| 配额管理 | ✅ 完成 | 请求数/token 数限制，Redis INCR + TTL |
| Admin API | ✅ 完成 | /api/v1/admin/tenants CRUD + 配额 + 用量 |
| Qdrant 商品库隔离 | ❌ 未做 | MVP 已够，后续优化 |
| OSS 存储隔离 | ❌ 未做 | 优先级低 |

**简历 bullet**：
```
· 设计并实现多租户架构：基于 X-Tenant-ID 在 Redis key 前缀层做会话/记忆隔离，
  支持租户级配额管理（请求数/token 数），为多团队/多店铺共用后端提供数据隔离能力。
```

### ❌ 阶段四：产品体验（未开始）

| 任务 | 状态 |
|------|------|
| Query rewrite / 槽位填充 | ❌ |
| 结构化过滤（price/category） | ❌ |
| 断线续传 | ❌ |

**优先级**：低，面试前不急着做

### ❌ 阶段五：部署运维（未开始）

| 任务 | 状态 |
|------|------|
| Docker 部署文档 | ❌ |
| CI/CD 配置 | ❌ |
| 线上监控 | ❌ |

**优先级**：中，用户之前提到要加

---

## 二、待验证/优化项

### 🔴 高优先级（面试前建议验证）

| 任务 | 当前状态 | 预期效果 | 验证方式 |
|------|---------|---------|---------|
| Context Recall 提升 | 加了兜底逻辑，未重测 | 0.404 → 0.6+ | 重跑 RAGAS 评测 |
| 多租户 API 测试 | 代码完成，未端到端测试 | 验证隔离 + 配额拦截 | curl 测试 |
| Redis checkpointer 验证 | 代码完成，未测试双进程 | 验证多副本共享会话 | 启动两个 backend 进程 |

### 🟡 中优先级（时间富余再做）

| 任务 | 说明 |
|------|------|
| Qdrant 商品库租户隔离 | payload filter 方案，1-2 天 |
| 部署文档 | Docker Compose + nginx 配置，1 天 |
| 压力测试报告 | 并发场景 + 失败场景，1 天 |

### 🟢 低优先级（概念学透即可）

| 任务 | 说明 |
|------|------|
| Prometheus 指标 | 锦上添花 |
| image 评测用例 | 需要下载图片 + 构造用例 |
| Perceptual Memory | 图片特征存储，优先级最低 |

---

## 三、面试准备状态

### ✅ 已准备

- [x] 电梯演讲（30 秒版本）
- [x] 技术架构图 + 选型理由
- [x] 核心指标速查表
- [x] 8 个高频面试问题预演
- [x] 简历 bullet（当前版 + 增强版）
- [x] 反吹牛提醒

### ⚠️ 需要补充

- [ ] 多租户相关的面试问题预演（隔离策略、配额管理、降级策略）
- [ ] 部署相关的面试问题（Docker、CI/CD、监控）
- [ ] 实际演示 Demo（启动后端 + 前端 + 多租户测试）

---

## 四、可以后台执行的任务

### 1. RAGAS 重测（验证 Context Recall 提升）
```bash
# 后台运行，预计 5-10 分钟
PYTHONPATH=$(pwd) backend/.venv/bin/python3 rag/eval/ragas_eval_real_v2.py --num-cases 10 --top-k 3
```
**目的**：验证 Context Recall 是否从 0.404 提升到 0.6+

### 2. 多租户 API 端到端测试
```bash
# 1. 启动后端（后台）
cd backend && source .venv/bin/activate && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. 创建测试租户
curl -X POST http://localhost:8000/api/v1/admin/tenants \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "test_team", "name": "测试团队"}'

# 3. 测试租户隔离
curl -X POST http://localhost:8000/api/v1/chat \
  -H "X-Tenant-ID: test_team" \
  -H "Content-Type: application/json" \
  -d '{"text": "推荐篮球鞋"}'
```
**目的**：验证多租户隔离 + 配额管理是否正常工作

### 3. Redis checkpointer 双进程测试
```bash
# 1. 启动第一个 backend（端口 8001）
cd backend && source .venv/bin/activate && python -m uvicorn app.main:app --host 0.0.0.0 --port 8001

# 2. 启动第二个 backend（端口 8002）
cd backend && source .venv/bin/activate && python -m uvicorn app.main:app --host 0.0.0.0 --port 8002

# 3. 在 8001 创建会话
curl -X POST http://localhost:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "推荐篮球鞋", "session_id": "sess_shared"}'

# 4. 在 8002 续接同一会话
curl -X POST http://localhost:8002/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "继续刚才的推荐", "session_id": "sess_shared"}'
```
**目的**：验证 Redis checkpointer 支持多副本共享会话

### 4. 部署文档编写
```bash
# 创建 docs/deployment.md
# 内容：Docker Compose 配置 + nginx 反向代理 + 环境变量说明
```
**目的**：面试时可以展示部署能力

---

## 五、建议执行顺序

### 今天（2026-07-01 晚间）
1. ✅ 后台运行 RAGAS 重测（5-10 分钟）
2. ✅ 后台启动后端测试多租户 API（10 分钟）
3. 补充多租户面试问题预演（30 分钟）

### 明天（2026-07-02）
1. 验证 Redis checkpointer 双进程测试（30 分钟）
2. 编写部署文档（1-2 小时）
3. 完整 Demo 演练（30 分钟）

### 面试前 2-3 天
1. 模拟面试（找朋友或用 AI 模拟）
2. 复习八股文（网络/OS/Python 基础）
3. 刷题（LeetCode 中等难度）

---

## 六、风险提示

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Context Recall 没提升 | 面试时被追问 | 准备话术："加了兜底逻辑，预期提升，但还没重测" |
| 多租户 API 有 bug | 演示失败 | 提前测试，准备降级方案（无 X-Tenant-ID 走 default） |
| Redis checkpointer 不工作 | 无法展示多副本 | 准备话术："代码完成，但还没做双进程测试" |

---

## 七、总结

**当前完成度**：
- 阶段一：80%（够用）
- 阶段二：90%（很强）
- 阶段三：70%（MVP 完成，亮点）
- 阶段四/五：0%（不影响面试）

**面试竞争力**：
- ✅ 技术深度够（RAG/LangGraph/Redis/多租户）
- ✅ 有量化指标（Top-3 69%、Faithfulness 0.82）
- ✅ 有工程化经验（持久化/治理/可观测/多租户）
- ⚠️ 缺少部署经验（可以补）
- ⚠️ 缺少线上经验（PoC 项目正常）

**一句话**：**现在投简历已经够用，但建议花 1-2 天补齐验证和文档，让面试更有底气。**
