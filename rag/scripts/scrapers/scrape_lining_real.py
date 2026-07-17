"""爬取李宁官网 store.lining.com 真实羽毛球鞋图片。

策略（绕过反爬软封禁）：
  - 搜索 API 直连 requests 会被软封禁（返回 dataList=[]）。浏览器会话能过。
  - 用 Playwright 渲染页面 + 拦截真实搜索响应（page.on response）累积商品池，
    滚动触发分页拿更多。
  - 图片 CDN（腾讯 COS）不软封禁，用 requests 直接下载。

接口（已逆向）：
  POST https://api.store.lining.com/goodsg/v1/goods-jh-query/search/lining/list/page
  入参 {"source":"4","saasId":"8324992625302181585","pageNum":N,"pageSize":25,
        "field":"","sortBy":1,"query":"<关键词>","filter":null}
  响应 data.dataList[]：title / spuPrice.minSalePrice(分) / spuVOList[0].primaryImage(干净 jpg)

流程：渲染→累积池→逐 product_id 匹配/兜底→下载图→重写 products.csv
（image_url 改本地静态路径 /api/v1/images/{pid}.jpg；name/price 同步真实值；
 description/category 不动，保 RAG 与评测稳定）

用法：PYTHONPATH=<root> python -m rag.scripts.scrape_lining_real
"""

import asyncio
import csv
import json
import logging
import os
import re
from io import BytesIO
from typing import Any

import requests
from PIL import Image
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)
IMAGES_DIR = os.path.join(_RAG_DIR, "data", "images")
PRODUCTS_CSV = os.path.join(_RAG_DIR, "data", "products.csv")

CHROME = "/home/user/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"
SEARCH_URL = "https://store.lining.com/goods/list?key=%E7%BE%BD%E6%AF%9B%E7%90%83%E9%9E%8B"
SEARCH_API_KEY = "search/lining/list/page"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
LOCAL_URL_TMPL = "/api/v1/images/{pid}.jpg"


async def gather_pool(page_size_target: int = 35) -> list[dict[str, Any]]:
    """Playwright 渲染 + 拦截搜索响应，滚动累积真实鞋款池。
    若浏览器被反爬软封（验证码/空响应），回退到已持久化的真实鞋池 JSON。
    """
    pool: list[dict[str, Any]] = []
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(executable_path=CHROME, headless=True,
                                        args=["--no-sandbox", "--disable-gpu"])
            ctx = await b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900})
            page = await ctx.new_page()

            async def on_resp(resp):
                if SEARCH_API_KEY in resp.url:
                    try:
                        j = await resp.json()
                        inner = (j.get("data") or {}).get("data") or {}
                        dl = inner.get("dataList", []) or []
                        if dl:
                            pool.extend(dl)
                            logger.info("捕获 +%d（累计 %d）", len(dl), len(pool))
                    except Exception as e:
                        logger.debug("resp 解析 %s", e)

            page.on("response", lambda r: asyncio.create_task(on_resp(r)))
            try:
                await page.goto(SEARCH_URL, wait_until="networkidle", timeout=60000)
            except Exception as e:
                logger.warning("goto 警告 %s", e)
            for _ in range(20):
                if len(pool) >= page_size_target:
                    break
                await page.evaluate("window.scrollBy(0, 2000)")
                await asyncio.sleep(1.0)
            await b.close()
    except Exception as e:
        logger.warning("浏览器采集异常 %s", e)

    # 回退：浏览器被封时用持久化的真实鞋池（rag/data/lining_shoes_pool.json）
    if not pool:
        fallback = os.path.join(_RAG_DIR, "data", "lining_shoes_pool.json")
        if os.path.exists(fallback):
            logger.warning("浏览器未捕获（可能触发验证码），回退到持久化真实鞋池 %s", fallback)
            try:
                raw = json.load(open(fallback, encoding="utf-8"))
                # 兼容两种结构：[{data:{data:{dataList}}}] 或 {data:{data:{dataList}}}
                first = raw[0] if isinstance(raw, list) else raw
                pool = (first.get("data") or {}).get("data", {}).get("dataList", []) or []
            except Exception as e:
                logger.error("读取回退池失败 %s", e)
    return pool


def clean_image_url(item: dict[str, Any]) -> str:
    """优先 spuVOList[0].primaryImage（干净 jpg），否则 primaryImage 去处理参数。"""
    spu_list = item.get("spuVOList") or []
    if spu_list and spu_list[0].get("primaryImage"):
        return spu_list[0]["primaryImage"]
    p = item.get("primaryImage") or ""
    return p.split("?")[0] if p else ""


def parse_price(item: dict[str, Any]) -> int | None:
    sp = item.get("spuPrice") or {}
    try:
        return int(int(sp.get("minSalePrice")) / 100)
    except (TypeError, ValueError):
        return None


def model_keyword(name: str) -> str:
    return re.sub(r"\s*羽毛球鞋\s*", "", name).strip()


def loose_match(title: str, keyword: str) -> bool:
    if not keyword:
        return False
    t = title.replace(" ", "").replace("　", "")
    k = keyword.replace(" ", "").replace("　", "")
    return k in t


def download_image(url: str, out_path: str) -> bool:
    if not url:
        return False
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            if r.status_code != 200 or len(r.content) < 1024:
                logger.warning("图片 HTTP %s %s", r.status_code, url[:60])
                continue
            Image.open(BytesIO(r.content)).convert("RGB").save(out_path, "JPEG", quality=92)
            return True
        except Exception as e:
            logger.warning("图片异常 %s (%s)", url[:60], e)
    return False


async def main():
    os.makedirs(IMAGES_DIR, exist_ok=True)
    with open(PRODUCTS_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    logger.info("products.csv 共 %d 行", len(rows))

    # 1. 浏览器累积真实鞋池
    pool = await gather_pool(page_size_target=35)
    seen_spu: set[str] = set()
    clean_pool: list[dict[str, Any]] = []
    for it in pool:
        sid = str(it.get("spuId"))
        if sid in seen_spu or not clean_image_url(it):
            continue
        seen_spu.add(sid)
        clean_pool.append(it)
    logger.info("去重+有图后池 %d 双", len(clean_pool))
    if not clean_pool:
        logger.error("池为空，放弃。检查网络/反爬。")
        return

    used_spu: set[str] = set()
    pool_idx = 0
    matched = 0
    fallback = 0
    updated_rows = []

    for row in rows:
        pid = row["product_id"]
        keyword = model_keyword(row["name"])
        chosen: dict[str, Any] | None = None

        # 2a. 池内按型号关键词松匹配
        for c in clean_pool:
            if str(c.get("spuId")) not in used_spu and loose_match(c.get("title", ""), keyword):
                chosen = c
                break
        # 2b. 池内轮询兜底
        if not chosen:
            while pool_idx < len(clean_pool):
                c = clean_pool[pool_idx]
                pool_idx += 1
                if str(c.get("spuId")) not in used_spu:
                    chosen = c
                    break

        if not chosen:
            logger.warning("[%s] 无可用真实鞋，保留原图", pid)
            updated_rows.append(row)
            continue

        used_spu.add(str(chosen.get("spuId")))
        is_match = loose_match(chosen.get("title", ""), keyword)
        out_path = os.path.join(IMAGES_DIR, f"{pid}.jpg")
        if download_image(clean_image_url(chosen), out_path):
            new_row = dict(row)
            new_row["image_url"] = LOCAL_URL_TMPL.format(pid=pid)
            new_row["name"] = chosen.get("title", row["name"]).strip()
            p = parse_price(chosen)
            if p:
                new_row["price"] = str(p)
            updated_rows.append(new_row)
            matched += 1 if is_match else 0
            fallback += 0 if is_match else 1
            logger.info("[%s] %s %s", pid, "命中" if is_match else "兜底", chosen.get("title", "")[:28])
        else:
            logger.warning("[%s] 图片下载失败，保留原 image_url", pid)
            updated_rows.append(row)

    fieldnames = list(rows[0].keys())
    with open(PRODUCTS_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(updated_rows)
    logger.info("完成：命中 %d / 兜底 %d / 共 %d", matched, fallback, len(rows))


if __name__ == "__main__":
    asyncio.run(main())
