# 李宁爬虫 v3 实现计划

> 对应设计文档: `docs/superpowers/specs/2026-06-17-lining-scraper-v3-design.md`  
> 目标脚本: `rag/scripts/lining_scraper_v3.py`  
> 预计工作量: 1-2 天

## 任务分解

### Task 1: 基础框架与配置（15min）

创建脚本骨架，包含：
- 模块导入与日志配置
- 路径常量（IMAGES_DIR, PRODUCTS_CSV, 缓存 JSON 路径）
- CLI 参数解析（argparse: --limit, --skip-list, --skip-detail, --category）
- Main 函数串联 5 个阶段

**验收**: `python -m rag.scripts.lining_scraper_v3 --help` 能正常输出帮助

---

### Task 2: Stage 1 - 列表抓取函数（30min）

实现 `fetch_product_list(category: str, limit: int) -> List[dict]`：

1. Playwright 启动浏览器（headless, Chrome UA, 1440x900）
2. 访问 `https://store.lining.com/goods/list?category={category}`
3. 注册 `page.on("response")` 拦截 `goods-jh-query/search/lining/list/page` 响应
4. 滚动页面触发分页，累积到 limit 条为止
5. 提取字段: spuId, title, primaryImage, spuPrice.minSalePrice
6. 保存到 `rag/data/lining_list_pool.json`
7. 关闭浏览器

**回退**: 如果浏览器被反爬（池为空），尝试从 `lining_list_pool.json` 读取缓存

**验收**: 运行后 `lining_list_pool.json` 包含指定数量的商品数据

---

### Task 3: Stage 2 - 详情抓取函数（45min）

实现 `fetch_product_detail(spu_id: str, page: Page) -> dict`：

1. 构造详情页 URL（需要从浏览器实际观察 URL 模式）
2. **API 优先策略**:
   - 注册 `page.on("response")` 监听可能的详情 API
   - 访问页面，检查是否有返回结构化 JSON 的 API
   - 如果找到，解析 JSON 获取 basic_info, introduction, images, price
3. **DOM 兜底策略**（如果 API 未找到）:
   - 等待页面加载完成
   - 用 CSS 选择器提取 4 个区域的内容:
     - `.basic___2XZcM` → basic_info
     - `.introduce___1xj8Z` → introduction
     - `.images___2uNMy` → image URLs
     - `.sales___3dWs7` → price
4. 保存到 `rag/data/lining_detail_cache.json`（spuId → detail dict）
5. 每次访问间隔 2~3 秒随机延迟

**断点恢复**: 如果 cache 已有该 spuId → 跳过

**验收**: 运行后 `lining_detail_cache.json` 包含详情数据

---

### Task 4: Stage 3 - 数据清洗函数（20min）

实现 `clean_product_data(raw_list: List[dict]) -> List[dict]`：

1. **文本清洗**:
   - `clean_html(text)`: 用正则去除 HTML 标签
   - `clean_whitespace(text)`: 连续空格→单空格, strip
   - `clean_special_chars(text)`: 去除零宽字符、不可见 Unicode
   - `fix_encoding(text)`: 处理 HTML 实体 (`&nbsp;` → 空格)

2. **字段标准化**:
   - `standardize_price(raw)`: `¥1,299` → `1299`, 分值 `/100` → 整数元
   - `extract_series(title)`: 从标题正则匹配系列名
   - `infer_gender(title, category)`: 从标题/分类推断性别

3. **去重**:
   - 精确去重: `spuId` 集合去重
   - 模糊去重: 去掉颜色后缀（如 `-1`, `-2`）后标题相同 → 保留第一条

**验收**: 清洗后无 HTML 残留、价格为整数、无重复 spuId

---

### Task 5: Stage 4 - 图片下载函数（20min）

实现 `download_images(product: dict, product_id: str) -> dict`：

1. **主图下载**:
   - URL: product["primaryImage"]
   - 保存: `rag/data/images/{product_id}.jpg`
   - 失败重试 3 次，格式转 RGB JPG，质量 92%

2. **详情图下载**:
   - URLs: product["detail_image_urls"]（列表）
   - 保存: `{product_id}_detail_1.jpg`, `{product_id}_detail_2.jpg`, ...
   - 去重（同一 URL 不重复下载）

3. **返回**: `{image_url: "/api/v1/images/{pid}.jpg", detail_images: "/api/v1/images/{pid}_detail_1.jpg;..."}`

**验收**: images 目录有对应文件，大小 > 1KB

---

### Task 6: Stage 5 - CSV 合并函数（20min）

实现 `merge_to_csv(new_products: List[dict])`：

1. 读取现有 `products.csv`（105 条）
2. 构建 spuId → row 索引
3. 对新数据每条:
   - 如果 spuId 已存在 → 更新该行字段
   - 如果不存在 → 追加新行，product_id 从 `lining_106` 续编
4. 新增字段填充: gender, series, spu_id, detail_images, basic_info, introduction, source_url
5. 写入临时文件 → 原子替换 `products.csv`

**验收**: CSV 行数 ≥ 105 + 新增行，新字段非空率 > 80%

---

### Task 7: 集成测试（30min）

1. 运行完整流程:
   ```bash
   PYTHONPATH=$(pwd) python -m rag.scripts.lining_scraper_v3 --limit 50
   ```

2. 验证各阶段产物:
   - `lining_list_pool.json` 有 ~100 条
   - `lining_detail_cache.json` 有详情数据
   - `images/` 目录有新增图片文件
   - `products.csv` 新增行正确

3. 验证断点恢复:
   - 中断后重新运行，确认跳过已处理的商品

4. 边界测试:
   - `--skip-detail` 只跑列表
   - `--skip-list` 只跑详情
   - `--category 男鞋` 只跑单分类

---

### Task 8: 文档与清理（10min）

1. 在脚本头部添加 docstring 说明用法
2. 更新 `CLAUDE.md` 中的 RAG 数据管道命令（加 v3 说明）
3. 确认日志输出清晰，有进度信息

## 实现顺序

建议按 Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 顺序实现，每个 Task 完成后做一次小验证。

## 关键技术决策

1. **详情页 URL 模式**: 需要在 Task 2 实现时先用浏览器手动访问一个详情页，观察 Network 面板确认 URL 格式和是否有 API
2. **CSS 选择器可能变化**: 李宁官网前端可能更新，选择器要写成可配置的常量
3. **反爬阈值**: 如果 50 双触发验证码，考虑降到 30 双或加大间隔

## 依赖

- `playwright` + chromium 浏览器
- `requests`
- `Pillow`
- 标准库: `csv`, `json`, `re`, `os`, `time`, `argparse`, `logging`
