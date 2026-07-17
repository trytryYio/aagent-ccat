import asyncio
# 尝试导入playwright，如果失败则提示安装
try:
    from playwright.async_api import async_playwright
except ImportError as e:
    import sys
    print("错误：无法导入playwright，请先执行以下命令安装：")
    print("1. pip install playwright")
    print("2. playwright install chromium")
    sys.exit(1)

# 使用 Playwright 探测李宁官网页面结构，辅助爬虫定位元素
async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        url = "https://store.lining.com/goods/list?key=%E7%BE%BD%E6%AF%9B%E7%90%83%E9%9E%8B"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="networkidle")
        await page.evaluate("window.scrollBy(0, 1000)")
        await asyncio.sleep(2)
        
        # Try to find elements with class main-pic
        elements = await page.evaluate("""
            () => {
                const results = [];
                const allImgs = document.querySelectorAll('img.main-pic');
                allImgs.forEach(img => {
                    results.push({
                        src: img.src,
                        className: img.className,
                        parentClass: img.parentElement ? img.parentElement.className : '',
                        grandParentClass: (img.parentElement && img.parentElement.parentElement) ? img.parentElement.parentElement.className : ''
                    });
                });
                return results;
            }
        """)
        
        print("Found elements with class 'main-pic':")
        for el in elements[:10]:
            print(f"Src: {el['src'][:50]}, Class: {el['className']}, Parent: {el['parentClass']}, GrandParent: {el['grandParentClass']}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect())
