import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://journaliststudio.google.com/pinpoint/search?collection=828502a5fcab2dc3"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(URL)

        print("\nIf prompted, log in in the browser.")
        print("When you can see the Pinpoint collection page, come back here and press ENTER.\n")
        input()

        await context.storage_state(path="pinpoint_state.json")
        print("✅ Saved session to pinpoint_state.json")
        await browser.close()

asyncio.run(main())
