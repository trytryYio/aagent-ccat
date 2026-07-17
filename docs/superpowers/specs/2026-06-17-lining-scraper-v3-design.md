# 李宁官网爬虫改进设计

> 日期: 2026-06-17  
> 状态: 设计完成，待用户审阅  
> 范围: 仅改进爬虫脚本，不涉及 RAG/Agent/前端

## 1. 背景与目标

### 现状问题
现有爬虫 (`scrape_lining_real.py`) 存在以下不足：
- 只通过关键词 `羽毛球鞋` 搜索，覆盖面窄
- 只获取标题、价格、主图，描述是模板生成的
- 不访问商品详情页，信息维度单一
- 没有男鞋/女鞋的分类维度

### 新目标
基于李宁官网分类浏览 + 详情页抓取，获取更丰富的真实商品数据：
- **分类覆盖**: 运动鞋_男鞋 + 运动鞋_女鞋
- **数据维度**: 基础信息、介绍信息、图片信息（多图）、价格信息
- **数据质量**: 经过清洗、去重、标准化
- **测试规模**: 男女各 50 双

## 2. 数据源

### 列表页
- URL: `https://store.lining.com/goods/list?category=运动鞋_男鞋` / `category=运动鞋_女鞋`
- API: `POST https://api.store.lining.com/goodsg/v1/goods-jh-query/search/lining/list/page`
- 返回: spuId, title, primaryImage, spuPrice 等

### 详情页
- URL: `https://store.lining.com/#/goods/detail/{spuId}` (由列表数据拼接)
- 数据提取策略: **API 优先 + DOM 兜底**
  - 优先: 用 Playwright 拦截详情页网络请求，寻找返回结构化数据的 API
  - 兜底: 用 CSS 选择器提取 DOM 内容

### 用户提供的 CSS 选择器（兜底用）
| 区域 | 选择器 |
|------|--------|
| 基础信息 | `#root > div.lining-page-container > div > div.goodsBody___2PBb1 > div.main___1c3Aa > div.content___L0UvG > div > div.basic___2XZcM` |
| 介绍信息 | `#root > div.lining-page-container > div > div.goodsBody___2PBb1 > div.main___1c3Aa > div.content___L0UvG > div > div.introduce___1xj8Z` |
| 图片信息 | `#root > div.lining-page-container > div > div.goodsBody___2PBb1 > div.main___1c3Aa > div.content___L0UvG > div > div.images___2uNMy` |
| 价格信息 | `#root > div.lining-page-container > div > div.goodsMain___34QvZ > div.info___1KGf6 > div:nth-child(2) > span.sales___3dWs7 > span:nth-child(2)` |

## 3. 架构设计

### 脚本: `rag/scripts/lining_scraper_v3.py`

```
Stage 1: 列表抓取 (fetch_product_list)
  ├── Playwright 打开分类页
  ├── 拦截列表 API 获取商品池
  ├── 每个分类取 50 双
  └── 持久化 → lining_list_pool.json

Stage 2: 详情抓取 (fetch_product_detail)
  ├── 逐个访问详情页
  ├── API 优先 + CSS 选择器兜底
  └── 持久化 → lining_detail_cache.json

Stage 3: 数据清洗 (clean_product_data)
  ├── 文本清洗: HTML 移除、空白处理、特殊字符、编码修复
  ├── 字段标准化: price→整数, series→正则提取
  ├── 精确去重: spuId 相同 → 保留一条
  ├── 模糊去重: 去颜色后缀后标题相同 → 保留主款
  └── 输出清洗后的数据列表

Stage 4: 图片下载 (download_images)
  ├── 主图 → {product_id}.jpg
  ├── 详情图 → {product_id}_detail_1.jpg, _detail_2.jpg, ...
  └── 去重、格式转换 (PNG→RGB JPG)、质量 92%

Stage 5: 数据合并 (merge_to_csv)
  ├── 读取现有 products.csv
  ├── 按 spuId/名称匹配已有行，更新字段
  ├── 匹配不上的追加新行 (product_id 续编)
  └── 写入新的 products.csv
```

### 运行参数
```bash
# 完整运行（男女各50双）
PYTHONPATH=$(pwd) python -m rag.scripts.lining_scraper_v3 --limit 50

# 只跑列表阶段
PYTHONPATH=$(pwd) python -m rag.scripts.lining_scraper_v3 --skip-detail

# 只跑详情阶段（利用已有列表缓存）
PYTHONPATH=$(pwd) python -m rag.scripts.lining_scraper_v3 --skip-list

# 只跑特定分类
PYTHONPATH=$(pwd) python -m rag.scripts.lining_scraper_v3 --category 男鞋
```

## 4. CSV 数据格式

### 字段定义

| 字段 | 类型 | 说明 | 来源 | 新旧 |
|------|------|------|------|------|
| product_id | string | `lining_XXX` | 生成 | 保留 |
| name | string | 商品名称 | 列表 API title | 保留 |
| description | string | 商品描述 | 详情页 introduction 清洗后 | 保留(内容更新) |
| category | string | 固定 `运动鞋` | 手动指定 | 保留 |
| price | string | 价格（整数元） | 详情页价格/API | 保留 |
| image_url | string | 主图本地路径 | 下载后 | 保留 |
| gender | string | `男` / `女` | 从分类参数推断 | **新增** |
| series | string | 系列名 | 正则提取 title | **新增** |
| spu_id | string | 李宁 SPU ID | 列表 API | **新增** |
| detail_images | string | 详情图路径，`;` 分隔 | 详情页图片区下载后 | **新增** |
| basic_info | string | 基础信息文本（清洗后） | 详情页 .basic 区 | **新增** |
| introduction | string | 详细介绍文本（清洗后） | 详情页 .introduce 区 | **新增** |
| source_url | string | 商品详情页原始 URL | 拼接 | **新增** |

### 示例行
```csv
product_id,name,description,category,price,image_url,gender,series,spu_id,detail_images,basic_info,introduction,source_url
lining_106,飞电6 CHALLENGER女子跑鞋ARMW004-1,全掌䨻科技 轻量高回弹 竞速比赛跑鞋,运动鞋,899,/api/v1/images/lining_106.jpg,女,飞电,11783475,/api/v1/images/lining_106_detail_1.jpg;/api/v1/images/lining_106_detail_2.jpg,鞋面:䨻丝科技 鞋底:全掌䨻+碳板,专业竞速跑鞋适合马拉松比赛,https://store.lining.com/#/goods/detail/xxx
```

### 多图存储
详情图用分号 `;` 分隔存储在 `detail_images` 字段中:
```
/api/v1/images/lining_106_detail_1.jpg;/api/v1/images/lining_106_detail_2.jpg;/api/v1/images/lining_106_detail_3.jpg
```

## 5. 数据清洗规则

| 清洗项 | 规则 |
|--------|------|
| HTML 移除 | 去除 `<br>`, `<p>`, `<div>`, `<span>` 等标签 |
| 空白处理 | 连续空格→单空格，首尾 trim，去除 `\n\r\t` |
| 特殊字符 | 去除不可见 Unicode、零宽字符 |
| 编码修复 | 统一 UTF-8，处理 `&nbsp;` `&amp;` 等 HTML 实体 |
| 价格标准化 | `¥1,299.00` → `1299`（整数元），API 返回的分值 → `/100` |
| 系列名提取 | 正则匹配已知系列: 贴地飞行/雷霆/刀锋/鹘鹰/影速/无敌号/战戟/风刃/疾风/音爆/飞电/绝影/烈骏/超轻/赤兔 等 |
| 精确去重 | spuId 相同 → 保留一条 |
| 模糊去重 | 去掉颜色/后缀后标题相同 → 保留主款 |

## 6. 反爬策略与错误处理

### 反爬策略
| 措施 | 说明 |
|------|------|
| 浏览器指纹 | 真实 Chrome UA + 1440x900 viewport |
| 请求间隔 | 详情页访问间隔 2~3 秒随机延迟 |
| Cookie 复用 | Playwright 上下文保持会话 |
| 超时控制 | 页面加载超时 30s，图片下载超时 20s |
| 指数退避 | 遇到 429/503 时等待 5s/15s/45s 重试 |

### 错误处理
- 列表页失败 → 重试 3 次 → 仍失败则尝试回退到持久化的列表 JSON
- 详情页单个失败 → 记录错误日志，跳过该商品继续下一个
- 图片下载失败 → 重试 3 次 → 仍失败则对应字段留空
- CSV 写入失败 → 先写临时文件再原子替换

### 断点恢复
- `rag/data/lining_list_pool.json` — 列表阶段产物
- `rag/data/lining_detail_cache.json` — 详情阶段产物

下次运行时:
- 如果列表 JSON 存在且数据量足够 → 跳过列表阶段
- 如果详情缓存已有某 spuId → 跳过该商品的详情抓取

## 7. 测试与验收

### 分阶段验证
1. 列表阶段: `lining_list_pool.json` 包含 50 男 + 50 女 = 100 条
2. 详情阶段: `lining_detail_cache.json` 每条有 basic_info / introduction / images / price
3. 清洗阶段: 无 HTML 残留、价格为整数、无重复 spuId
4. 图片测试: 文件数量正确、大小 > 1KB、JPEG 格式
5. 合并测试: CSV 行数 ≥ 105 + 新增行，新字段非空率 > 80%

### 验收标准
- 男女各至少 45 双成功抓取（允许个别失败）
- 详情字段 (basic_info / introduction) 非空率 > 80%
- 每双鞋至少 1 张主图下载成功
- CSV 无重复行
