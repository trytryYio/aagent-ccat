import asyncio
from playwright.async_api import async_playwright


async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://www.baidu.com")
        title = await page.title()
        print(f"Title: {title}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(test())
