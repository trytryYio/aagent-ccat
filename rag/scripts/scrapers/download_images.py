"""下载商品图片到 rag/data/images/，供 CLIP 向量化。

主方案：picsum.photos seeded 占位图（稳定可复现，CLIP 可编码）。
  - 李宁官网 store.lining.com 是 SPA 站，requests 拿不到商品数据，
    需 Playwright 动态渲染（见文末 lining_official_scraper 注释），
    但官网反爬/改版风险高，演示环境不依赖。
  - 真实场景接入 lining 图床时，替换 download_one 的 URL 来源即可。

用法：
  python -m rag.scripts.download_images
"""

import csv
import logging
import os
import time

import requests
from PIL import Image
from io import BytesIO

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)
IMAGES_DIR = os.path.join(_RAG_DIR, "data", "images")
PRODUCTS_CSV = os.path.join(_RAG_DIR, "data", "products.csv")

# picsum seeded：同一 seed 返回同一张图，可复现
PICSUM_TMPL = "https://picsum.photos/seed/{pid}/600/600"


def load_product_ids() -> list[str]:
    if not os.path.exists(PRODUCTS_CSV):
        logger.error("找不到 products.csv: %s", PRODUCTS_CSV)
        return []
    ids = []
    with open(PRODUCTS_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ids.append(row["product_id"])
    return ids


def download_one(pid: str) -> bool:
    out = os.path.join(IMAGES_DIR, f"{pid}.jpg")
    if os.path.exists(out) and os.path.getsize(out) > 1024:
        return True  # 已存在跳过
    url = PICSUM_TMPL.format(pid=pid)
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                logger.warning("%s HTTP %s, retry %d", pid, r.status_code, attempt)
                time.sleep(1)
                continue
            img = Image.open(BytesIO(r.content)).convert("RGB")
            img.save(out, "JPEG", quality=90)
            return True
        except Exception as e:
            logger.warning("%s 失败(%s), retry %d", pid, e, attempt)
            time.sleep(1)
    return False


def main():
    os.makedirs(IMAGES_DIR, exist_ok=True)
    pids = load_product_ids()
    if not pids:
        return
    logger.info("下载 %d 张图片到 %s", len(pids), IMAGES_DIR)
    ok = 0
    for pid in pids:
        if download_one(pid):
            ok += 1
            logger.info("ok %s", pid)
        time.sleep(0.3)  # 限速
    logger.info("完成: %d/%d", ok, len(pids))


# ── 可选升级：李宁官网真实商品图（需 Playwright） ──────────────────────
# store.lining.com 是 SPA，requests 拿到空壳。要用真实图需：
#   1. pip install playwright && playwright install chromium
#   2. 用 async_playwright 打开搜索页，等待商品卡片渲染
#   3. 提取 img.main-pic（或更新后的选择器）的 src
#   4. 按商品名匹配 product_id 下载
# 官网改版/反爬时选择器会失效，演示不建议依赖。
# 参考实现见 rag/scripts/lining_scraper_v2.py。


if __name__ == "__main__":
    main()
