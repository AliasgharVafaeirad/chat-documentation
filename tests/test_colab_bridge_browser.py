"""Offline browser fixture: checkpoint extraction without any download event."""

import asyncio
import base64
import io
import zipfile
from playwright.async_api import async_playwright
from colab_browser import collect_links


async def test():
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("results.json", '{"items": []}')
    payload = base64.b64encode(content.getvalue()).decode("ascii")
    name = "whisper_colab_results_" + "a" * 24 + "_000048.zip"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=False)
        page = await context.new_page()
        await page.route(
            "https://fixture-colab.googleusercontent.com/**",
            lambda route: route.fulfill(
                content_type="text/html",
                body=f'<pre data-backup-result="{name}" data-backup-payload="{payload}">Checkpoint ready</pre><a data-backup-result="{name}" download="{name}" href="data:application/zip;base64,{payload}">Legacy checkpoint</a><p>Waiting for upload part 2/2</p><input type="file">',
            ),
        )
        await page.goto("https://fixture-colab.googleusercontent.com/output")
        offered = set()
        received = []
        downloads = []
        page.on("download", lambda event: downloads.append(event))

        async def receive(filename, data):
            received.append((filename, data))

        await collect_links(page, offered, receive)
        assert received[0][0] == name
        with zipfile.ZipFile(io.BytesIO(received[0][1])) as archive:
            assert archive.read("results.json") == b'{"items": []}'
        await collect_links(page, offered, receive)
        assert len(offered) == 1
        assert len(received) == 1 and not downloads

        # A failed local save must remain eligible for retry.
        async def fail(filename, data):
            raise OSError("temporary disk error")

        offered.clear()
        try:
            await collect_links(page, offered, fail)
        except OSError:
            pass
        assert not offered
        await collect_links(page, offered, receive)
        assert len(received) == 2 and not downloads
        await browser.close()
    print(
        "Checkpoint extraction, deduplication and failed-save retry without browser downloads: passed"
    )


if __name__ == "__main__":
    asyncio.run(test())
