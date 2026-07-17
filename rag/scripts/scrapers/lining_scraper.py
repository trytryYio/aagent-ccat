import asyncio
import csv
import os
import requests
from io import BytesIO
from PIL import Image

# 尝试导入playwright，如果失败则提示安装
try:
    from playwright.async_api import async_playwright
except ImportError as e:
    import sys

    print("错误：无法导入playwright，请先执行以下命令安装：")
    print("1. pip install playwright")
    print("2. playwright install chromium")
    sys.exit(1)
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# 配置路径（自动检测项目根目录）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # rag/scripts/
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)  # rag/
_PROJECT_DIR = os.path.dirname(_RAG_DIR)  # Agent/
BASE_DIR = _PROJECT_DIR
DATA_DIR = os.path.join(BASE_DIR, "rag", "data")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
PRODUCTS_CSV = os.path.join(DATA_DIR, "products.csv")


# 自动化抓取李宁官网羽毛球鞋数据（名称、价格、高清图）并更新本地库
async def scrape_and_update():
    search_url = (
        "https://store.lining.com/goods/list?key=%E7%BE%BD%E6%AF%9B%E7%90%83%E9%9E%8B"
    )

    if not os.path.exists(IMAGES_DIR):
        os.makedirs(IMAGES_DIR)

    async with async_playwright() as p:
        logger.info("启动浏览器进行抓取...")
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            logger.info(f"正在访问: {search_url}")
            await page.goto(search_url, wait_until="networkidle", timeout=60000)

            # 增加滚动和等待时间，确保内容加载更多商品
            for i in range(10):
                await page.evaluate(f"window.scrollBy(0, 1000)")
                await asyncio.sleep(1.5)

            # 精细化抓取逻辑
            containers = await page.evaluate(
                """
                () => {
                    const items = [];
                    // 1. 获取所有带有 main-pic 类的图片
                    const mainPics = document.querySelectorAll('img.main-pic');
                    console.log(`Found ${mainPics.length} main-pic images`);
                    
                    mainPics.forEach((img, index) => {
                        // 往上找容器
                        let p = img.parentElement;
                        let name = '李宁羽毛球鞋';
                        let price = '0';
                        
                        // 在父级链中寻找名称和价格
                        for(let i=0; i<10; i++) {
                            if (!p) break;
                            
                            // 寻找价格
                            if (price === '0') {
                                const priceEl = p.querySelector('[class*="price"], .price, span[class*="price"]');
                                if (priceEl) price = priceEl.innerText.replace('￥', '').replace('¥', '').trim().split('\\n')[0];
                            }
                            
                            // 寻找名称
                            if (name === '李宁羽毛球鞋') {
                                const nameEl = p.querySelector('[class*="name"], [class*="title"], .goods-name');
                                if (nameEl) name = nameEl.innerText.trim().split('\\n')[0];
                            }
                            
                            if (price !== '0' && name !== '李宁羽毛球鞋') break;
                            p = p.parentElement;
                        }

                        if (img.src && !img.src.startsWith('data:')) {
                            items.push({
                                name: name,
                                price: price,
                                imgUrl: img.src
                            });
                        }
                    });
                    return items;
                }
            """
            )

            # 过滤无效数据
            containers = [
                c
                for c in containers
                if c["imgUrl"] and not c["imgUrl"].startswith("data:")
            ]
            logger.info(f"找到 {len(containers)} 个商品候选对象")

            scraped_data = []
            count = 0
            seen_urls = set()
            for i, item in enumerate(containers):
                if count >= 50:
                    break

                img_url = item["imgUrl"]
                if img_url in seen_urls:
                    continue
                seen_urls.add(img_url)

                # 优化图片质量
                if "?" in img_url:
                    img_url = img_url.split("?")[0]
                if not img_url.startswith("http"):
                    img_url = "https:" + img_url

                name = item["name"] or f"李宁羽毛球鞋 {count+1}"
                price = item["price"] or "399"  # 默认价格

                product_id = f"lining_{count+1:03d}"

                logger.info(f"正在处理 {product_id}: {name} (价格: {price})")
                success = download_and_convert_image(img_url, product_id)

                if success:
                    scraped_data.append(
                        {
                            "product_id": product_id,
                            "name": name,
                            "price": price,
                            "description": f"李宁官方正品 {name}，专业羽毛球运动设计，采用李宁核心缓震科技，极致抓地与轻盈包裹，助力赛场表现。",
                            "category": "运动/鞋类/羽毛球鞋",
                            "image_url": img_url,
                        }
                    )
                    count += 1

            # 更新 products.csv
            if scraped_data:
                with open(
                    PRODUCTS_CSV, mode="w", encoding="utf-8-sig", newline=""
                ) as f:
                    writer = csv.DictWriter(
                        f,
                        fieldnames=[
                            "product_id",
                            "name",
                            "price",
                            "description",
                            "category",
                            "image_url",
                        ],
                    )
                    writer.writeheader()
                    writer.writerows(scraped_data)
                logger.info(f"成功更新 {len(scraped_data)} 条商品信息至 {PRODUCTS_CSV}")
            else:
                logger.warning("未抓取到有效数据，未更新 CSV")

        except Exception as e:
            logger.error(f"抓取过程中发生错误: {e}")
        finally:
            await browser.close()


def download_and_convert_image(url, product_id):
    """下载图片并转换为 JPG 格式"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://store.lining.com/",
        }
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code != 200:
            logger.error(f"图片下载失败 (状态码 {response.status_code}): {url}")
            return False

        img = Image.open(BytesIO(response.content))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        save_path = os.path.join(IMAGES_DIR, f"{product_id}.jpg")
        img.save(save_path, "JPEG", quality=95)
        logger.info(f"图片已保存: {save_path}")
        return True
    except Exception as e:
        logger.error(f"图片处理失败 ({product_id}): {e}")
        return False


if __name__ == "__main__":
    asyncio.run(scrape_and_update())
