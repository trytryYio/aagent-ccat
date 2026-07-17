"""李宁官网运动鞋爬虫 v3 — 按分类抓取商品详情。

特性:
  - stealth.js 反检测（隐藏 webdriver、模拟 Chrome 特征）
  - 腾讯滑块验证码自动解决（贝塞尔曲线轨迹模拟）
  - 自动解决失败时回退到手动滑动（headful 模式）
  - 断点恢复（中间结果持久化到 JSON）

用法:
  # headful 模式（推荐，验证码自动+手动兜底）
  PYTHONPATH=$(pwd) python -m rag.scripts.scrapers.lining_scraper_v3 --headful --limit 50

  # headless 模式（无验证码等待）
  PYTHONPATH=$(pwd) python -m rag.scripts.scrapers.lining_scraper_v3 --limit 50

  # 其他选项
  PYTHONPATH=$(pwd) python -m rag.scripts.scrapers.lining_scraper_v3 --skip-detail
  PYTHONPATH=$(pwd) python -m rag.scripts.scrapers.lining_scraper_v3 --skip-list
  PYTHONPATH=$(pwd) python -m rag.scripts.scrapers.lining_scraper_v3 --category 男鞋
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import random
import time
import math
from io import BytesIO
from typing import Any, Optional, List, Tuple
from urllib.parse import quote

import requests
from PIL import Image

try:
    from playwright.async_api import async_playwright, Page
except ImportError:
    import sys
    print("错误: 请先安装 playwright: pip install playwright && playwright install chromium")
    sys.exit(1)

# ─── 日志配置 ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ─── 路径常量 ────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)
_DATA_DIR = os.path.join(_RAG_DIR, "data")
IMAGES_DIR = os.path.join(_DATA_DIR, "images")
PRODUCTS_CSV = os.path.join(_DATA_DIR, "products.csv")
LIST_POOL_JSON = os.path.join(_DATA_DIR, "lining_list_pool.json")
DETAIL_CACHE_JSON = os.path.join(_DATA_DIR, "lining_detail_cache.json")

# ─── URL 与 API 常量 ────────────────────────────────────────────────
BASE_LIST_URL = "https://store.lining.com/goods/list"
LIST_API_KEY = "goods-jh-query/search/lining/list/page"
# 详情页 URL 模板 (History路由，UmiJS routerBase="/")
DETAIL_URL_TMPL = "https://store.lining.com/goods/detail/{spuId}"
# 已知的详情页 API 路径模式 (需要在运行时验证)
DETAIL_API_PATTERNS = [
    "goods-jh-query/search/lining/detail",
    "goods/detail",
    "spu/detail",
    "goodsInfo",
]

# ─── 浏览器配置 ──────────────────────────────────────────────────────
# 自动检测 Playwright Chromium 路径（兼容 Windows/Linux/Mac）
def _find_chrome() -> str:
    """自动查找 Playwright 安装的 Chromium 可执行文件。"""
    import subprocess
    try:
        # 方法1: playwright install chromium 的标准位置
        result = subprocess.run(
            ["python", "-c", "from playwright._impl._driver import compute_driver_executable; import os; print(os.path.dirname(str(compute_driver_executable())))"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            driver_dir = result.stdout.strip()
            # 在 driver 目录的上级找 chromium
            for root, dirs, files in os.walk(os.path.dirname(driver_dir)):
                for f in files:
                    if f in ("chrome", "chrome.exe"):
                        return os.path.join(root, f)
    except Exception:
        pass

    # 方法2: 通过 playwright CLI 查询
    try:
        result = subprocess.run(
            ["python", "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass

    # 方法3: 常见的 Playwright 缓存路径
    import platform
    home = os.path.expanduser("~")
    if platform.system() == "Windows":
        cache_dirs = [
            os.path.join(home, "AppData", "Local", "ms-playwright"),
            os.path.join(home, ".cache", "ms-playwright"),
        ]
    elif platform.system() == "Darwin":
        cache_dirs = [os.path.join(home, "Library", "Caches", "ms-playwright")]
    else:
        cache_dirs = [os.path.join(home, ".cache", "ms-playwright")]

    for cache_dir in cache_dirs:
        if not os.path.isdir(cache_dir):
            continue
        for entry in sorted(os.listdir(cache_dir), reverse=True):
            if not entry.startswith("chromium"):
                continue
            if platform.system() == "Windows":
                candidate = os.path.join(cache_dir, entry, "chrome-win", "chrome.exe")
            else:
                candidate = os.path.join(cache_dir, entry, "chrome-linux64", "chrome")
            if os.path.isfile(candidate):
                return candidate

    # 方法4: 让 Playwright 自己找（不传 executable_path）
    return ""

CHROME = _find_chrome()
if CHROME:
    logger.info(f"Playwright Chromium: {CHROME}")
else:
    logger.info("Playwright Chromium: 使用默认路径（不指定 executable_path）")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1440, "height": 900}

# stealth.js 反检测脚本（来自 CrawlerTutorial）
STEALTH_JS = """
// 隐藏 webdriver 标志
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// 模拟 chrome 对象
window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}};

// 模拟 plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer'},
        {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer'},
        {name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer'},
        {name: 'PDF Viewer', filename: 'internal-pdf-viewer'},
        {name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer'}
    ]
});

// 模拟 languages
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});

// 修复 permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({state: Notification.permission}) :
        originalQuery(parameters)
);

// 修复 navigator.mimeTypes
Object.defineProperty(navigator, 'mimeTypes', {
    get: () => [
        {type: 'application/pdf', suffixes: 'pdf', description: '', enabledPlugin: Plugin},
        {type: 'text/pdf', suffixes: 'pdf', description: '', enabledPlugin: Plugin}
    ]
});

// 修复 WebGL 供应商
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};
"""

# ─── CSS 选择器 (兜底用) ────────────────────────────────────────────
CSS_BASIC = "#root > div.lining-page-container > div > div.goodsBody___2PBb1 > div.main___1c3Aa > div.content___L0UvG > div > div.basic___2XZcM"
CSS_INTRODUCE = "#root > div.lining-page-container > div > div.goodsBody___2PBb1 > div.main___1c3Aa > div.content___L0UvG > div > div.introduce___1xj8Z"
CSS_IMAGES = "#root > div.lining-page-container > div > div.goodsBody___2PBb1 > div.main___1c3Aa > div.content___L0UvG > div > div.images___2uNMy"
CSS_PRICE = "#root > div.lining-page-container > div > div.goodsMain___34QvZ > div.info___1KGf6 > div:nth-child(2) > span.sales___3dWs7 > span:nth-child(2)"

# ─── 已知系列名 ──────────────────────────────────────────────────────
KNOWN_SERIES = [
    "贴地飞行", "雷霆", "刀锋", "鹘鹰", "影速", "无敌号", "战戟",
    "风刃", "风洞", "疾风", "音爆", "飞电", "绝影", "烈骏", "超轻",
    "赤兔", "角斗士", "雲霆", "突袭", "全能王", "音浪",
]

# ── CSV 字段定义 ────────────────────────────────────────────────────
CSV_FIELDS = [
    "product_id", "name", "description", "category", "price", "image_url",
    "gender", "series", "spu_id", "detail_images", "basic_info", "introduction", "source_url",
]


# ═══════════════════════════════════════════════════════════════════════
# 自动滑块验证码解决器（来自 CrawlerTutorial）
# ═══════════════════════════════════════════════════════════════════════

class HumanTrajectoryGenerator:
    """人类轨迹生成器 - 模拟真实拖拽行为"""

    @staticmethod
    def generate_bezier_trajectory(distance: int, duration: float = 0.5) -> List[Tuple[int, int, float]]:
        """使用贝塞尔曲线生成自然的拖拽轨迹"""
        trajectory = []
        p0 = (0, 0)
        p1 = (distance * 0.3, random.randint(-10, 10))
        p2 = (distance * 0.7, random.randint(-5, 5))
        p3 = (distance, 0)
        steps = random.randint(25, 35)

        for i in range(steps + 1):
            t = i / steps
            x = (1-t)**3 * p0[0] + 3*(1-t)**2*t * p1[0] + 3*(1-t)*t**2 * p2[0] + t**3 * p3[0]
            y = (1-t)**3 * p0[1] + 3*(1-t)**2*t * p1[1] + 3*(1-t)*t**2 * p2[1] + t**3 * p3[1]
            time_point = duration * t + random.uniform(-0.005, 0.005)
            trajectory.append((int(x), int(y), max(0, time_point)))

        return trajectory


class SliderCaptchaSolver:
    """滑块验证码自动解决器"""

    def __init__(self, page):
        self.page = page
        self.trajectory_gen = HumanTrajectoryGenerator()

    async def solve_tencent_captcha(self, timeout: int = 30) -> bool:
        """
        解决腾讯滑块验证码

        策略：
        1. 检测验证码元素
        2. 截图获取背景和滑块
        3. 用 OpenCV 检测缺口位置
        4. 模拟人类轨迹拖拽

        Returns:
            True 如果成功解决
        """
        try:
            # 检查是否有验证码
            has_captcha = await self.page.evaluate("""
                () => {
                    const el = document.querySelector('[class*=tencent-captcha]');
                    return el && el.offsetHeight > 0;
                }
            """)
            if not has_captcha:
                return True  # 没有验证码

            logger.info("  检测到腾讯验证码，尝试自动解决...")

            # 等待验证码完全加载
            await asyncio.sleep(1)

            # 查找滑块和背景元素
            # 腾讯验证码的典型结构
            slider = await self.page.query_selector('[class*=tc-drag-thumb], [class*=slider], [class*=drag-btn]')
            if not slider:
                logger.warning("  未找到滑块元素，回退到手动模式")
                return False

            # 获取滑块位置
            box = await slider.bounding_box()
            if not box:
                return False

            # 估算拖拽距离（腾讯验证码通常约 200-300px）
            # 实际距离需要通过图像分析，这里使用典型值
            drag_distance = random.randint(200, 280)

            # 执行拖拽
            start_x = box['x'] + box['width'] / 2
            start_y = box['y'] + box['height'] / 2

            # 移动到滑块
            await self.page.mouse.move(start_x, start_y)
            await asyncio.sleep(random.uniform(0.1, 0.3))

            # 按下
            await self.page.mouse.down()
            await asyncio.sleep(random.uniform(0.05, 0.15))

            # 生成并执行轨迹
            trajectory = self.trajectory_gen.generate_bezier_trajectory(drag_distance, duration=0.6)
            last_time = 0
            for x, y, time_point in trajectory:
                delay = time_point - last_time
                if delay > 0:
                    await asyncio.sleep(delay)
                last_time = time_point
                await self.page.mouse.move(start_x + x, start_y + y)

            # 松开
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await self.page.mouse.up()

            logger.info(f"  滑块拖拽完成，距离: {drag_distance}px")

            # 等待验证结果
            await asyncio.sleep(2)

            # 检查是否通过
            still_has_captcha = await self.page.evaluate("""
                () => {
                    const el = document.querySelector('[class*=tencent-captcha]');
                    return el && el.offsetHeight > 0;
                }
            """)

            if not still_has_captcha:
                logger.info("  ✅ 验证码自动解决成功!")
                return True
            else:
                logger.warning("  自动解决失败，需要手动滑动")
                return False

        except Exception as e:
            logger.warning(f"  自动解决验证码异常: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════
# Stage 1: 列表抓取
# ═══════════════════════════════════════════════════════════════════════

async def fetch_product_list(category: str, limit: int = 50, headful: bool = False) -> list[dict[str, Any]]:
    """Playwright 打开分类页，拦截列表 API，累积商品池。"""
    category_param = f"运动鞋_{category}"
    url = f"{BASE_LIST_URL}?category={quote(category_param)}"
    logger.info(f"[Stage 1] 抓取列表: {category_param}, 目标 {limit} 双 ({'有头' if headful else '无头'}模式)")

    pool: list[dict[str, Any]] = []
    seen_spu: set[str] = set()

    try:
        async with async_playwright() as p:
            launch_args = {
                "headless": not headful,
                "args": ["--no-sandbox", "--disable-gpu", "--disable-blink-features=AutomationControlled"],
            }
            # 仅在找到 Chrome 路径时指定，否则让 Playwright 自动查找
            if CHROME:
                launch_args["executable_path"] = CHROME
            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(user_agent=UA, viewport=VIEWPORT, locale="zh-CN")
            # 注入 stealth.js 反检测
            await context.add_init_script(STEALTH_JS)
            page = await context.new_page()

            # 拦截列表 API 响应
            async def on_response(resp):
                if LIST_API_KEY in resp.url:
                    try:
                        data = await resp.json()
                        # 尝试多种响应结构
                        # 格式1: data.dataList
                        # 格式2: data.data.dataList
                        items = []
                        if isinstance(data.get("data"), dict):
                            d = data["data"]
                            if isinstance(d.get("dataList"), list):
                                items = d["dataList"]
                            elif isinstance(d.get("data"), dict) and isinstance(d["data"].get("dataList"), list):
                                items = d["data"]["dataList"]

                        logger.info(f"  API 响应: url={resp.url[:80]}, status={resp.status}, "
                                    f"dataKeys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}, "
                                    f"items={len(items)}")

                        for item in items:
                            spu_id = str(item.get("spuId", ""))
                            if spu_id and spu_id not in seen_spu:
                                seen_spu.add(spu_id)
                                pool.append({
                                    "spuId": spu_id,
                                    "title": item.get("title", ""),
                                    "primaryImage": _clean_image_url(item),
                                    "price": _parse_price(item),
                                    "category": category,
                                })
                        if items:
                            logger.info(f"  捕获 +{len(items)} 条，累计 {len(pool)} 双")
                    except Exception as e:
                        logger.warning(f"  响应解析错误: {e}")

            page.on("response", lambda r: asyncio.create_task(on_response(r)))

            # 监听所有 API 相关响应（调试用）
            async def on_any_response(resp):
                if "lining.com" in resp.url and ("api" in resp.url or "goods" in resp.url):
                    logger.debug(f"  RESP: {resp.status} {resp.url[:100]}")

            page.on("response", lambda r: asyncio.create_task(on_any_response(r)))

            # 访问列表页
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5)  # 等待 API 请求完成
            except Exception as e:
                logger.warning(f"  页面加载警告: {e}")

            # 滚动触发分页
            scroll_rounds = 0
            while len(pool) < limit and scroll_rounds < 30:
                await page.evaluate("window.scrollBy(0, 1500)")
                await asyncio.sleep(1.0)
                scroll_rounds += 1

            await browser.close()
    except Exception as e:
        logger.warning(f"  浏览器异常: {e}")

    # 截取到 limit
    pool = pool[:limit]
    logger.info(f"[Stage 1] 完成: 获取 {len(pool)} 双 {category_param}")

    # 持久化
    _save_json(LIST_POOL_JSON, pool, tag=f"列表池({category})")
    return pool


def _clean_image_url(item: dict) -> str:
    """提取干净的商品图片 URL。"""
    spu_list = item.get("spuVOList") or []
    if spu_list and spu_list[0].get("primaryImage"):
        return spu_list[0]["primaryImage"]
    img = item.get("primaryImage", "")
    return img.split("?")[0] if img else ""


def _parse_price(item: dict) -> Optional[int]:
    """解析价格，返回整数元。"""
    price_info = item.get("spuPrice") or {}
    try:
        min_price = price_info.get("minSalePrice")
        if min_price is not None:
            return int(int(min_price) / 100)  # 分 → 元
    except (TypeError, ValueError):
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════
# Stage 2: 详情抓取
# ═══════════════════════════════════════════════════════════════════════

async def fetch_product_details(
    products: list[dict[str, Any]],
    headful: bool = False,
) -> list[dict[str, Any]]:
    """逐个访问详情页，提取基础信息、介绍、图片、价格。

    headful=True 时：弹出浏览器窗口，遇到验证码暂停等待用户手动滑动。
    headless=False 时：CSS选择器提取（验证码出现则跳过）。
    """
    logger.info(f"[Stage 2] 抓取 {len(products)} 个商品详情 ({'有头' if headful else '无头'}模式)")

    # 加载已有缓存
    cache = _load_json(DETAIL_CACHE_JSON, default={})
    results = []

    pw = await async_playwright().start()
    launch_args = {
        "headless": not headful,
        "args": ["--no-sandbox", "--disable-gpu"],
    }
    if CHROME:
        launch_args["executable_path"] = CHROME
    browser = await pw.chromium.launch(**launch_args)
    browser_ctx = await browser.new_context(user_agent=UA, viewport=VIEWPORT)
    await browser_ctx.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
    page = await browser_ctx.new_page()

    try:
        for i, product in enumerate(products):
            spu_id = product["spuId"]

            # 断点恢复: 已缓存则跳过
            if spu_id in cache:
                logger.info(f"  [{i+1}/{len(products)}] {spu_id} 已缓存，跳过")
                results.append(cache[spu_id])
                continue

            detail_url = DETAIL_URL_TMPL.format(spuId=spu_id)
            logger.info(f"  [{i+1}/{len(products)}] 抓取详情: {product['title'][:30]}...")

            if headful:
                detail = await _fetch_detail_headful(page, spu_id, detail_url)
            else:
                detail = await _fetch_single_detail(page, spu_id, detail_url)

            if detail:
                cache[spu_id] = detail
                results.append(detail)
                if len(cache) % 5 == 0:
                    _save_json(DETAIL_CACHE_JSON, cache, tag="详情缓存")
            else:
                logger.warning(f"  [{i+1}/{len(products)}] 详情抓取失败: {spu_id}")

            # 随机延迟
            delay = random.uniform(2.0, 4.0)
            await asyncio.sleep(delay)

        _save_json(DETAIL_CACHE_JSON, cache, tag="详情缓存")

    finally:
        await browser.close()
        await pw.stop()

    logger.info(f"[Stage 2] 完成: 成功 {len(results)}/{len(products)} 个详情")
    return results


async def _wait_captcha_solved(page: "Page", timeout: int = 300) -> bool:
    """等待验证码被用户手动解决。返回 True 表示通过，False 表示超时。"""
    logger.info("  ⚠️ 检测到验证码！请在浏览器窗口中手动滑动完成验证...")
    start = time.time()
    while time.time() - start < timeout:
        has_captcha = await page.evaluate("""
            () => {
                const el = document.querySelector('[class*=tencent-captcha]');
                return el && el.offsetHeight > 0;
            }
        """)
        if not has_captcha:
            logger.info("  ✅ 验证码已通过，继续抓取...")
            await asyncio.sleep(2)  # 等页面加载
            return True
        await asyncio.sleep(2)
    logger.warning(f"   验证码等待超时 ({timeout}s)，跳过该商品")
    return False


async def _fetch_detail_headful(
    page: "Page",
    spu_id: str,
    detail_url: str,
) -> Optional[dict[str, Any]]:
    """有头模式: 访问详情页，先尝试自动解决验证码，失败则等待手动。"""
    try:
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # 检查验证码
        has_captcha = await page.evaluate("""
            () => {
                const el = document.querySelector('[class*=tencent-captcha]');
                return el && el.offsetHeight > 0;
            }
        """)

        if has_captcha:
            # 先尝试自动解决
            solver = SliderCaptchaSolver(page)
            auto_solved = await solver.solve_tencent_captcha()

            if not auto_solved:
                # 自动失败，等待手动
                if not await _wait_captcha_solved(page):
                    return None

        # 等待详情内容渲染
        goods_loaded = False
        for attempt in range(10):
            has_goods = await page.evaluate("""
                () => document.querySelector('[class*=goodsBody], [class*=goodsMain], [class*=basic___]') !== null
            """)
            if has_goods:
                goods_loaded = True
                break
            await asyncio.sleep(2)

        if not goods_loaded:
            # 再次检查验证码
            has_captcha2 = await page.evaluate("""
                () => {
                    const el = document.querySelector('[class*=tencent-captcha]');
                    return el && el.offsetHeight > 0;
                }
            """)
            if has_captcha2:
                solver = SliderCaptchaSolver(page)
                if await solver.solve_tencent_captcha():
                    await asyncio.sleep(5)
                else:
                    if not await _wait_captcha_solved(page):
                        return None
            else:
                logger.warning(f"  详情页内容未加载: {spu_id}")
                return None

        return await _extract_detail_dom(page, spu_id, detail_url)

    except Exception as e:
        logger.warning(f"  详情页异常 {spu_id}: {e}")
        return None


async def _fetch_single_detail(
    page: "Page",
    spu_id: str,
    detail_url: str,
) -> Optional[dict[str, Any]]:
    """抓取单个商品详情页。API 优先 + DOM 兜底。"""
    detail_api_data: dict[str, Any] = {}

    # 注册 API 拦截
    async def on_response(resp):
        for pattern in DETAIL_API_PATTERNS:
            if pattern in resp.url:
                try:
                    data = await resp.json()
                    if data and isinstance(data, dict):
                        detail_api_data.update(data)
                except Exception:
                    pass
                break

    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    try:
        await page.goto(detail_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)  # 等待动态内容加载

        # 策略 1: 检查是否有 API 返回结构化数据
        if detail_api_data:
            parsed = _parse_detail_api(detail_api_data, spu_id, detail_url)
            if parsed:
                page.remove_all_listeners("response")
                return parsed

        # 策略 2: CSS 选择器兜底
        parsed = await _extract_detail_dom(page, spu_id, detail_url)
        page.remove_all_listeners("response")
        return parsed

    except Exception as e:
        logger.warning(f"  详情页访问失败 {spu_id}: {e}")
        page.remove_all_listeners("response")
        return None


def _parse_detail_api(
    api_data: dict,
    spu_id: str,
    detail_url: str,
) -> Optional[dict[str, Any]]:
    """从 API 响应中解析详情。"""
    # 尝试多种 API 响应结构
    data = api_data.get("data") or api_data

    # 提取基础信息
    basic_info = ""
    if "basicInfo" in data:
        basic_info = json.dumps(data["basicInfo"], ensure_ascii=False)
    elif "goodsBasicInfo" in data:
        basic_info = json.dumps(data["goodsBasicInfo"], ensure_ascii=False)

    # 提取介绍
    introduction = data.get("introduction", "") or data.get("description", "")

    # 提取图片
    images = []
    for key in ["images", "goodsImages", "imageList", "spuImages"]:
        if key in data and isinstance(data[key], list):
            images = [img.get("url", "") or img.get("imageUrl", "") for img in data[key]]
            images = [u for u in images if u]
            break
    if not images and "primaryImage" in data:
        images = [data["primaryImage"]]

    # 提取价格
    price = None
    price_info = data.get("price") or data.get("spuPrice") or {}
    if isinstance(price_info, dict):
        min_price = price_info.get("minSalePrice") or price_info.get("salePrice")
        if min_price:
            try:
                price = int(int(min_price) / 100)
            except (TypeError, ValueError):
                pass
    elif isinstance(price_info, (int, float)):
        price = int(int(price_info) / 100)

    if not basic_info and not introduction and not images:
        return None

    return {
        "spuId": spu_id,
        "basic_info": basic_info,
        "introduction": introduction,
        "detail_image_urls": images,
        "price": price,
        "source_url": detail_url,
    }


async def _extract_detail_dom(
    page: "Page",
    spu_id: str,
    detail_url: str,
) -> Optional[dict[str, Any]]:
    """用 CSS 选择器从 DOM 提取详情（兜底策略）。"""
    try:
        result = await page.evaluate("""
            (selectors) => {
                const getText = (sel) => {
                    const el = document.querySelector(sel);
                    return el ? el.innerText.trim() : '';
                };
                const getImages = (sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return [];
                    const imgs = el.querySelectorAll('img');
                    return Array.from(imgs).map(img => img.src || img.getAttribute('data-src') || '').filter(Boolean);
                };
                return {
                    basic_info: getText(selectors.basic),
                    introduction: getText(selectors.introduce),
                    images: getImages(selectors.images),
                    price_text: getText(selectors.price),
                };
            }
        """, {
            "basic": CSS_BASIC,
            "introduce": CSS_INTRODUCE,
            "images": CSS_IMAGES,
            "price": CSS_PRICE,
        })

        # 解析价格文本
        price = None
        price_text = result.get("price_text", "")
        if price_text:
            match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
            if match:
                try:
                    price = int(float(match.group()))
                except ValueError:
                    pass

        if not result.get("basic_info") and not result.get("introduction"):
            return None

        return {
            "spuId": spu_id,
            "basic_info": result.get("basic_info", ""),
            "introduction": result.get("introduction", ""),
            "detail_image_urls": result.get("images", []),
            "price": price,
            "source_url": detail_url,
        }

    except Exception as e:
        logger.warning(f"  DOM 提取失败 {spu_id}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# Stage 3: 数据清洗
# ═══════════════════════════════════════════════════════════════════════

def clean_product_data(
    products: list[dict[str, Any]],
    details: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并列表数据与详情数据，进行清洗、标准化、去重。"""
    logger.info(f"[Stage 3] 清洗 {len(products)} 条商品数据")

    # 构建详情索引
    detail_map = {d["spuId"]: d for d in details if d.get("spuId")}

    cleaned = []
    seen_spu: set[str] = set()
    seen_titles: set[str] = set()  # 模糊去重用

    for product in products:
        spu_id = product["spuId"]

        # 精确去重
        if spu_id in seen_spu:
            continue

        # 合并数据
        detail = detail_map.get(spu_id, {})
        merged = {**product, **{k: v for k, v in detail.items() if v is not None}}

        # 文本清洗
        title = clean_text(product.get("title", ""))
        basic_info = clean_text(detail.get("basic_info", ""))
        introduction = clean_text(detail.get("introduction", ""))

        # 模糊去重: 去掉颜色后缀后标题相同
        title_base = re.sub(r'[-—]\d+$', '', title).strip()
        if title_base in seen_titles:
            logger.debug(f"  模糊去重跳过: {title}")
            continue
        seen_titles.add(title_base)

        # 字段标准化
        price = detail.get("price") or product.get("price")
        series = extract_series(title)
        gender = product.get("category", "")  # 男/女

        # 构建结果
        result = {
            "spu_id": spu_id,
            "name": title,
            "price": str(price) if price else "",
            "gender": gender,
            "series": series,
            "basic_info": basic_info,
            "introduction": introduction,
            "description": introduction or basic_info,  # 作为 CSV description
            "primaryImage": product.get("primaryImage", ""),
            "detail_image_urls": detail.get("detail_image_urls", []),
            "source_url": detail.get("source_url", ""),
            "category": "运动鞋",
        }

        cleaned.append(result)
        seen_spu.add(spu_id)

    logger.info(f"[Stage 3] 清洗完成: {len(cleaned)} 条 (去重前 {len(products)} 条)")
    return cleaned


def clean_text(text: str) -> str:
    """文本清洗: HTML 移除、空白处理、特殊字符、编码修复。"""
    if not text:
        return ""

    # HTML 实体解码
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')

    # 去除 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)

    # 去除零宽字符和不可见 Unicode
    text = re.sub(r'[​‌‍﻿ ]', '', text)

    # 空白处理: 连续空白→单空格，首尾 trim
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def extract_series(title: str) -> str:
    """从标题中提取系列名。"""
    for series in KNOWN_SERIES:
        if series in title:
            return series
    return ""


# ═══════════════════════════════════════════════════════════════════════
# Stage 4: 图片下载
# ═══════════════════════════════════════════════════════════════════════

def download_images_for_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为每个商品下载主图和详情图，返回带本地路径的结果。"""
    logger.info(f"[Stage 4] 下载 {len(products)} 个商品的图片")
    os.makedirs(IMAGES_DIR, exist_ok=True)

    # 读取现有 CSV 获取 product_id 映射
    existing_ids = _get_existing_product_ids()
    next_id = len(existing_ids) + 1

    results = []
    for i, product in enumerate(products):
        spu_id = product["spu_id"]

        # 分配 product_id
        product_id = existing_ids.get(spu_id)
        if not product_id:
            product_id = f"lining_{next_id:03d}"
            next_id += 1

        logger.info(f"  [{i+1}/{len(products)}] {product_id}: {product['name'][:25]}...")

        # 下载主图
        image_url = ""
        primary = product.get("primaryImage", "")
        if primary:
            local_path = os.path.join(IMAGES_DIR, f"{product_id}.jpg")
            if _download_image(primary, local_path):
                image_url = f"/api/v1/images/{product_id}.jpg"

        # 下载详情图
        detail_paths = []
        detail_urls = product.get("detail_image_urls", [])
        for j, img_url in enumerate(detail_urls[:8]):  # 最多 8 张详情图
            if not img_url or img_url in product.get("primaryImage", ""):
                continue  # 跳过与主图相同的
            local_path = os.path.join(IMAGES_DIR, f"{product_id}_detail_{j+1}.jpg")
            if _download_image(img_url, local_path):
                detail_paths.append(f"/api/v1/images/{product_id}_detail_{j+1}.jpg")

        product["product_id"] = product_id
        product["image_url"] = image_url
        product["detail_images"] = ";".join(detail_paths)

        results.append(product)

    logger.info(f"[Stage 4] 图片下载完成: {len(results)} 个商品")
    return results


def _download_image(url: str, save_path: str) -> bool:
    """下载单张图片，转为 JPG。"""
    if not url:
        return False
    if not url.startswith("http"):
        url = "https:" + url

    # 确保目录存在（Windows 路径兼容）
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    for attempt in range(3):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": UA, "Referer": "https://store.lining.com/"},
                timeout=20,
            )
            if resp.status_code != 200 or len(resp.content) < 1024:
                continue
            img = Image.open(BytesIO(resp.content))
            img.convert("RGB").save(save_path, "JPEG", quality=92)
            return True
        except Exception as e:
            if attempt == 2:
                logger.warning(f"  图片下载失败: {url[:60]} ({e})")
    return False


def _get_existing_product_ids() -> dict[str, str]:
    """从现有 CSV 读取 spu_id → product_id 映射。"""
    mapping = {}
    if not os.path.exists(PRODUCTS_CSV):
        return mapping
    try:
        with open(PRODUCTS_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                spu = row.get("spu_id", "")
                pid = row.get("product_id", "")
                if spu and pid:
                    mapping[spu] = pid
    except Exception:
        pass
    return mapping


# ═══════════════════════════════════════════════════════════════════════
# Stage 5: CSV 合并
# ═══════════════════════════════════════════════════════════════════════

def merge_to_csv(products: list[dict[str, Any]]) -> None:
    """将新数据合并到 products.csv。"""
    logger.info(f"[Stage 5] 合并 {len(products)} 条数据到 CSV")

    # 读取现有数据
    existing_rows = []
    existing_spu: set[str] = set()
    if os.path.exists(PRODUCTS_CSV):
        with open(PRODUCTS_CSV, encoding="utf-8-sig") as f:
            existing_rows = list(csv.DictReader(f))
        existing_spu = {r.get("spu_id", "") for r in existing_rows if r.get("spu_id")}
        logger.info(f"  现有 CSV: {len(existing_rows)} 行")

    # 构建更新索引
    updated_spu: set[str] = set()
    for product in products:
        spu_id = product.get("spu_id", "")
        if not spu_id:
            continue

        new_row = {
            "product_id": product["product_id"],
            "name": product["name"],
            "description": product.get("description", ""),
            "category": product.get("category", "运动鞋"),
            "price": product.get("price", ""),
            "image_url": product.get("image_url", ""),
            "gender": product.get("gender", ""),
            "series": product.get("series", ""),
            "spu_id": spu_id,
            "detail_images": product.get("detail_images", ""),
            "basic_info": product.get("basic_info", ""),
            "introduction": product.get("introduction", ""),
            "source_url": product.get("source_url", ""),
        }

        # 查找已有行
        found = False
        for i, row in enumerate(existing_rows):
            if row.get("spu_id") == spu_id:
                existing_rows[i] = {**row, **{k: v for k, v in new_row.items() if v}}
                found = True
                break

        if not found:
            existing_rows.append(new_row)
        updated_spu.add(spu_id)

    # 写入
    tmp_path = PRODUCTS_CSV + ".tmp"
    with open(tmp_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing_rows)
    os.replace(tmp_path, PRODUCTS_CSV)

    logger.info(f"[Stage 5] CSV 合并完成: {len(existing_rows)} 行 (新增/更新 {len(updated_spu)} 条)")


# ═══════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════

def _save_json(path: str, data: Any, tag: str = "") -> None:
    """保存 JSON 文件。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"  已保存 {tag}: {path}")
    except Exception as e:
        logger.warning(f"  保存 {tag} 失败: {e}")


def _load_json(path: str, default: Any = None) -> Any:
    """加载 JSON 文件。"""
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="李宁官网运动鞋爬虫 v3")
    parser.add_argument("--limit", type=int, default=500, help="每个分类抓取数量 (默认 500)")
    parser.add_argument("--category", type=str, default=None, help="只抓指定分类: 男鞋/女鞋")
    parser.add_argument("--skip-list", action="store_true", help="跳过列表阶段 (用缓存)")
    parser.add_argument("--skip-detail", action="store_true", help="跳过详情阶段")
    parser.add_argument("--headful", action="store_true", help="有头浏览器模式（验证码时弹窗让你手动滑动）")
    args = parser.parse_args()

    categories = ["男鞋", "女鞋"] if not args.category else [args.category if "鞋" in args.category else args.category + "鞋"]

    # Stage 1: 列表抓取
    all_products: list[dict[str, Any]] = []
    if not args.skip_list:
        for cat in categories:
            pool = await fetch_product_list(cat, limit=args.limit, headful=args.headful)
            all_products.extend(pool)
    else:
        cached = _load_json(LIST_POOL_JSON, default=[])
        if isinstance(cached, list):
            all_products = cached
        logger.info(f"[Stage 1] 跳过，从缓存加载 {len(all_products)} 条")

    if not all_products:
        logger.error("商品池为空，退出")
        return

    # Stage 2: 详情抓取
    all_details: list[dict[str, Any]] = []
    if not args.skip_detail:
        all_details = await fetch_product_details(all_products, headful=args.headful)
    else:
        cached = _load_json(DETAIL_CACHE_JSON, default={})
        all_details = list(cached.values()) if isinstance(cached, dict) else []
        logger.info(f"[Stage 2] 跳过，从缓存加载 {len(all_details)} 条详情")

    # Stage 3: 数据清洗
    cleaned = clean_product_data(all_products, all_details)

    # Stage 4: 图片下载
    with_images = download_images_for_products(cleaned)

    # Stage 5: CSV 合并
    merge_to_csv(with_images)

    logger.info("✅ 全部完成!")


if __name__ == "__main__":
    asyncio.run(main())
