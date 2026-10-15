import asyncio
from playwright.async_api import async_playwright
from colab_browser import click_text


async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("""<button>Run all</button>
        <div role="menuitem" command="runall" onclick="this.dataset.clicked='1'">
        Run all<span>Ctrl+F9</span></div>""")
        assert await click_text(page, ["Run all"])
        assert await page.locator('[command="runall"]').get_attribute("data-clicked") == "1"
        await browser.close()
        print("Run all with shortcut and duplicate button: passed")


if __name__ == "__main__":
    asyncio.run(test())
