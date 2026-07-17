# 2026-07-01 下午冲刺进展记录

> 时间：2026-07-01 14:00 - 18:00  
> 目标：面试前快速收尾 + 多租户架构 MVP

---

## 完成清单

### 1. RAGAS 评测链路修复
- **问题**：Answer Relevancy 始终为 0.0
- **根因**：`ragas_eval_real_v2.py` 的 `DashScopeEmbeddingWrapper` 没有继承 LangChain `Embeddings` 基类，导致 `LangchainEmbeddingsWrapper` 初始化失败
- **解决**：改用 `langchain_core.embeddings.Embeddings` 自定义子类包装
- **验证**：embedding 维度 1024，正常返回向量
- **文件**：`rag/eval/ragas_eval_real_v2.py`

### 2. LLM Fallback 独立配置
- **问题**：fallback 模型还在用主模型的 API key/base_url，DeepSeek 挂了切 qwen-plus 也不生效
- **解决**：
  - `config.py` 增加 `llm_fallback_api_key`、`llm_fallback_base_url`、`llm_fallback_model`
  - `llm.py` 的 `_get_llm_raw()` 支持传入独立的 api_key/base_url
  - `.env` 配置 fallback 为通义千问 qwen-plus（DashScope）
- **验证**：主模型 DeepSeek 正常响应，fallback 配置正确加载
- **文件**：`backend/app/config.py`、`backend/app/graph/llm.py`、`backend/.env`

### 3. Context Recall 优化
- **问题**：Context Recall 0.404，检索到的上下文覆盖不足 ground truth
- **根因**：没有保证每个候选商品至少有一条引用，高相关性商品占满 citation 位置，其他商品的 ground truth 信息丢失
- **解决**：在 `get_citations()` 末尾加兜底逻辑，对未覆盖的候选商品补充 top-1 引用
- **文件**：`rag/text_retrieval.py`
- **预期**：Context Recall 从 0.404 提升到 0.6+（待重测验证）

### 4. 评测文档整理
- **产出**：
  - `docs/eval/README.md`：评测结果汇总（RAGAS 四维 + 检索评测）
  - `docs/eval/latest_ragas_report.json`：最新 RAGAS 报告
  - `docs/eval/latest_retrieval_eval.md`：检索评测报告
- **用途**：面试时快速展示指标

### 5. 面试准备文档
- **产出**：`docs/interview/面试准备.md`
- **内容**：
  - 30 秒电梯演讲
  - 技术架构图 + 选型理由
  - 核心指标速查表
  - 8 个高频面试问题预演（RAG vs LLM、向量检索瓶颈、LangGraph vs LangChain 等）
  - 简历 bullet（当前版 + 增强版）
  - 反吹牛提醒（什么能写、什么不能写）

### 6. 多租户架构 MVP
- **核心能力**：
  - ✅ 租户 CRUD（创建/查询/删除）
  - ✅ 会话隔离（Redis key 前缀 `agent:tenant:{tenant_id}:session:{sid}`）
  - ✅ 记忆隔离（四层记忆全链路传递 tenant_id）
  - ✅ 配额管理（请求数/token 数限制，Redis INCR + TTL）
  - ✅ Admin API（`/api/v1/admin/tenants` CRUD + 配额 + 用量）
  - ✅ 向后兼容（无 X-Tenant-ID 走 default 租户）
- **核心文件**：
  - `app/models.py`：+ Tenant/TenantQuota/TenantUsage
  - `app/core/tenant_manager.py`：租户管理（CRUD + 配额）
  - `app/core/tenant_middleware.py`：X-Tenant-ID 解析中间件
  - `app/core/session.py`：会话管理（+ 租户隔离）
  - `app/graph/memory.py`：四层记忆（+ tenant_id 传递）
  - `app/api/chat.py`：聊天接口（+ tenant_id 提取）
  - `app/api/admin.py`：租户管理 API（新增）
  - `app/main.py`：注册 TenantMiddleware + admin_router
- **文档**：`docs/multi_tenant.md`（架构设计 + API 接口 + 测试用例）
- **验证**：
  - 创建测试租户 `test_team_a`
  - 配额检查正常
  - Redis key 格式正确（`agent:tenant:test_team_a:meta` 等）

---

## 未完成（低优先级）

| 任务 | 状态 | 原因 |
|------|------|------|
| RAGAS 重测验证 Context Recall 提升 | ⏸️ | 优先级调低，面试前不急 |
| 多租户 Qdrant 商品库隔离 | ⏸️ | MVP 已够，后续优化 |
| Prometheus 指标 | ⏸️ | 锦上添花 |

---

## 技术决策记录

### 为什么多租户用 Header 而不是 JWT？
- MVP 阶段简化实现
- Header 方案对前端调试友好
- 后续可叠加 JWT，不冲突

### 为什么商品库暂不隔离？
- 50 SKU 规模下，独立 collection 管理成本过高
- 先验证会话/记忆隔离
- 后续可在 Qdrant payload 中加 `tenant_id` 字段，检索时 filter

### 为什么 LLM fallback 用通义千问？
- DashScope 已有 API key（rerank 在用）
- 通义千问中文效果好
- 成本可控

---

## Git 提交记录

```
TODO: 需要提交
- feat: 多租户架构 MVP（租户隔离 + 配额管理 + Admin API）
- fix: Answer Relevancy 评测修复（embedding wrapper 兼容性问题）
- feat: LLM fallback 独立配置（DeepSeek → 通义千问）
- perf: Context Recall 优化（每个候选商品至少一条引用兜底）
- docs: 评测文档整理 + 面试准备文档
```

---

## 面试素材更新

### 简历 bullet（多租户）
```
· 设计并实现多租户架构：基于 X-Tenant-ID 在 Redis key 前缀层做会话/记忆隔离，
  支持租户级配额管理（请求数/token 数），为多团队/多店铺共用后端提供数据隔离能力。
```

### 面试可以讲的点
1. **多租户隔离策略**：Redis key 前缀 vs Qdrant collection vs payload filter 的 trade-off
2. **配额管理**：Redis INCR + TTL 的原子性 vs 极端并发下的精度问题
3. **向后兼容**：没有 X-Tenant-ID 走 default 租户，老接口无需改动
4. **降级策略**：Redis 挂了 TenantManager 降级为内存模式

---

## 下一步

1. **Git 提交**：把今天的改动提交到 develop 分支
2. **测试多租户**：启动后端，用 curl 测试租户隔离和配额拦截
3. **更新 todo.md**：把多租户加进去
4. **准备面试**：过一遍 `docs/interview/面试准备.md`
