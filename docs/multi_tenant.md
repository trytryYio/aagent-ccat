# 多租户架构实施文档

> 实施日期：2026-07-01  
> 版本：v1.0 (MVP)

---

## 一、架构设计

### 1.1 数据隔离策略

| 层级 | 隔离方式 | Redis Key 格式 |
|------|---------|---------------|
| **会话数据** | Redis key 前缀 | `agent:tenant:{tenant_id}:session:{session_id}` |
| **用户偏好** | Redis key 前缀 | `agent:tenant:{tenant_id}:prefs:{session_id}` |
| **租户元数据** | Redis Hash | `agent:tenant:meta:{tenant_id}` |
| **用量计数** | Redis String + TTL | `agent:tenant:usage:req:{tenant_id}:{YYYY-MM-DD}` |
| **商品库** | 暂不隔离（MVP） | 所有租户共享 Qdrant collection |

**为什么商品库暂不隔离？**
- MVP 阶段优先验证会话/记忆隔离
- 50 SKU 规模下，独立 collection 管理成本过高
- 后续可在 Qdrant payload 中加 `tenant_id` 字段，检索时 filter

### 1.2 租户标识传递

**方案**：HTTP Header `X-Tenant-ID`

```bash
# 前端请求示例
curl -X POST http://localhost:8000/api/v1/chat \
  -H "X-Tenant-ID: test_team_a" \
  -H "Content-Type: application/json" \
  -d '{"text": "推荐一双篮球鞋", "session_id": "sess_123"}'
```

**为什么不用 JWT？**
- MVP 阶段简化实现，后续可叠加 JWT
- Header 方案对前端调试友好

### 1.3 配额管理

| 配额类型 | 默认值 | 计数方式 |
|---------|--------|---------|
| 每日请求数 | 1000 | Redis INCR + 24h TTL |
| 每月 token 数 | 1,000,000 | Redis INCRBY + 31 天 TTL |
| 存储量 | 100 MB | 未实现（需 OSS 集成） |

---

## 二、核心文件清单

| 文件 | 职责 |
|------|------|
| `app/models.py` | Tenant/TenantQuota/TenantUsage 模型定义 |
| `app/core/tenant_manager.py` | 租户 CRUD + 用量计数 + 配额检查 |
| `app/core/tenant_middleware.py` | 解析 X-Tenant-ID → 校验 → 注入 request.state |
| `app/core/session.py` | 会话管理（Redis key 加租户前缀） |
| `app/graph/memory.py` | 四层记忆（全链路传递 tenant_id） |
| `app/api/chat.py` | 从 request.state 提取 tenant_id |
| `app/api/admin.py` | 租户管理接口（CRUD + 配额 + 用量） |
| `app/main.py` | 注册 TenantMiddleware + admin_router |

---

## 三、API 接口

### 3.1 业务接口（需 X-Tenant-ID）

```bash
# 创建对话（自动创建会话）
POST /api/v1/chat
Header: X-Tenant-ID: test_team_a
Body: {"text": "推荐篮球鞋"}
Response: {
  "session_id": "sess_abc123",
  "tenant_id": "test_team_a",  # 新增字段
  ...
}
```

### 3.2 管理接口（无需 X-Tenant-ID）

```bash
# 列出所有租户
GET /api/v1/admin/tenants

# 创建租户
POST /api/v1/admin/tenants
Body: {
  "tenant_id": "team_b",
  "name": "团队 B",
  "plan": "pro",
  "quota": {
    "max_requests_per_day": 500,
    "max_tokens_per_month": 500000
  }
}

# 查询租户详情
GET /api/v1/admin/tenants/{tenant_id}

# 更新配额
PUT /api/v1/admin/tenants/{tenant_id}/quota
Body: {"max_requests_per_day": 2000}

# 查询用量
GET /api/v1/admin/tenants/{tenant_id}/usage

# 删除租户（软删除）
DELETE /api/v1/admin/tenants/{tenant_id}
```

---

## 四、测试用例

### 4.1 租户隔离验证

```bash
# 1. 创建两个租户
curl -X POST http://localhost:8000/api/v1/admin/tenants \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "team_a", "name": "Team A"}'

curl -X POST http://localhost:8000/api/v1/admin/tenants \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "team_b", "name": "Team B"}'

# 2. Team A 创建会话
curl -X POST http://localhost:8000/api/v1/chat \
  -H "X-Tenant-ID: team_a" \
  -H "Content-Type: application/json" \
  -d '{"text": "推荐篮球鞋", "session_id": "sess_shared"}'

# 3. Team B 尝试访问同一 session_id（应该看到空会话）
curl -X POST http://localhost:8000/api/v1/chat \
  -H "X-Tenant-ID: team_b" \
  -H "Content-Type: application/json" \
  -d '{"text": "我的历史对话呢？", "session_id": "sess_shared"}'

# 4. 检查 Redis key
redis-cli keys "agent:tenant:team_*:session:*"
# 应该看到：
# - agent:tenant:team_a:session:sess_shared
# - agent:tenant:team_b:session:sess_shared（独立的 key）
```

### 4.2 配额超限测试

```bash
# 1. 创建低配额租户
curl -X POST http://localhost:8000/api/v1/admin/tenants \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "limited_team",
    "name": "受限租户",
    "quota": {"max_requests_per_day": 2}
  }'

# 2. 发送 3 次请求（第 3 次应返回 429）
for i in {1..3}; do
  curl -X POST http://localhost:8000/api/v1/chat \
    -H "X-Tenant-ID: limited_team" \
    -H "Content-Type: application/json" \
    -d "{\"text\": \"测试 $i\"}"
  echo ""
done

# 第 3 次响应：
# {"error": "quota_exceeded", "message": "今日请求数已达上限（2）"}
```

---

## 五、简历 bullet（完成后启用）

```
· 设计并实现多租户架构：基于 X-Tenant-ID 在 Redis key 前缀层做会话/记忆隔离，
  支持租户级配额管理（请求数/token 数），为多团队/多店铺共用后端提供数据隔离能力。
```

---

## 六、后续优化方向

| 优化项 | 优先级 | 工作量 |
|--------|--------|--------|
| Qdrant 商品库按租户隔离（payload filter） | P1 | 2-3 天 |
| JWT token 携带 tenant_id（替代 Header） | P2 | 1 天 |
| 租户级 OSS 存储隔离（图片上传） | P2 | 2 天 |
| 管理后台 UI（租户列表/用量图表） | P3 | 3-5 天 |
| 租户自助注册（无需管理员创建） | P3 | 2 天 |

---

## 七、注意事项

1. **向后兼容**：没有 X-Tenant-ID 的请求自动走 `default` 租户
2. **Redis 挂了怎么办**：TenantManager 降级为内存模式（但无法跨进程共享）
3. **配额检查时机**：在 TenantMiddleware 中拦截，不进入业务逻辑
4. **用量计数精度**：Redis INCR 是原子操作，但极端并发下可能有少量误差（可接受）
5. **软删除**：DELETE 租户只是改状态为 `deleted`，不物理删除数据（便于审计）
