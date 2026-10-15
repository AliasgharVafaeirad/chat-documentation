import unittest
from test_senders import SenderTests
import messenger_adapters as adapters
import main


class ActivationTests(SenderTests):
    async def test_trusted_click_captures_payload_once_without_scroll(self):
        await self.page.set_content(
            '<div id="panel" style="height:200px;overflow:auto"><div class="File"><div class="file-icon-container" style="width:80px;height:80px">download</div></div><div style="height:1000px"></div></div>'
        )
        await self.page.evaluate(main.CAPTURE_DOWNLOADS)
        await self.page.evaluate("""() => {
   window.__backupCapture.active=true;window.clicks=0;
   document.querySelector('.file-icon-container').addEventListener('click',event=>{
    if(!event.isTrusted) return;
    window.clicks++;
    const a=document.createElement('a');a.download='test.rar';
    a.href=URL.createObjectURL(new Blob(['archive-test']));a.click();
   });
  }""")
        before = await self.page.locator("#panel").evaluate("(e)=>e.scrollTop")
        self.assertTrue(
            await adapters.activate_visible(
                self.page, self.page.locator(".file-icon-container"), main
            )
        )
        state = await self.page.evaluate(main.CAPTURE_STATUS)
        self.assertEqual(len(state), 1)
        self.assertTrue(state[0]["ready"])
        self.assertEqual(state[0]["size"], 12)
        self.assertEqual(await self.page.evaluate("window.clicks"), 1)
        self.assertEqual(await self.page.locator("#panel").evaluate("(e)=>e.scrollTop"), before)

    async def test_hidden_download_not_clicked_or_scrolled(self):
        await self.page.set_content(
            '<div id="panel" style="height:100px;overflow:auto"><div style="height:500px"></div><button id="download">Download</button></div>'
        )
        self.assertFalse(
            await adapters.activate_visible(self.page, self.page.locator("#download"), main)
        )
        self.assertEqual(await self.page.locator("#panel").evaluate("(e)=>e.scrollTop"), 0)

    async def test_native_browser_download_saved_by_full_adapter(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        await self.page.set_content(
            '<div id="panel" data-backup-panel="selected"><div class="Message" data-backup-token="backup-0"><div class="File"><div class="file-icon-container" style="width:80px;height:80px">download</div></div></div></div>'
        )
        await self.page.evaluate("""() => {
   window.__baleBackupBox=document.querySelector('#panel');
   window.clicks=0;
   document.querySelector('.file-icon-container').addEventListener('click',event=>{
    if(!event.isTrusted) return;window.clicks++;
    const a=document.createElement('a');a.download='test.rar';
    a.href=URL.createObjectURL(new Blob(['archive-test']));a.click();
   });
  }""")

        async def assert_selection(page):
            pass

        shared = SimpleNamespace(
            CAPTURE_DOWNLOADS=main.CAPTURE_DOWNLOADS,
            CAPTURE_STATUS=main.CAPTURE_STATUS,
            CAPTURE_CHUNK=main.CAPTURE_CHUNK,
            VISIBLE_CLICK_POINT=main.VISIBLE_CLICK_POINT,
            DownloadIncomplete=main.DownloadIncomplete,
            assert_selection=assert_selection,
        )
        item = {
            "id": "123",
            "token": "backup-0",
            "media": [{"kind": "document", "name": "test.rar", "count": 1}],
            "attachment_results": [],
        }
        checkpoints = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "downloads"
            folder.mkdir()
            await adapters.download(
                self.page, item, folder, {"_checkpoint": lambda: checkpoints.append(True)}, shared
            )
            files = list(folder.glob("*.rar"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b"archive-test")
            self.assertEqual(len(item["attachment_results"]), 1)
            self.assertEqual(await self.page.evaluate("window.clicks"), 1)
            self.assertEqual(len(checkpoints), 1)

    async def test_explicit_document_menu_avoids_preload_icon(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        await self.page.set_content(
            '<div id="panel" data-backup-panel="selected" style="height:500px"><div class="Message" data-backup-token="backup-0"><div class="File"><div class="file-icon-container" style="width:80px;height:80px">preload only</div></div></div><div class="Menu open" hidden><button class="MenuItem">Download</button></div></div>'
        )
        await self.page.evaluate("""() => {
   window.__baleBackupBox=document.querySelector('#panel');window.iconClicks=0;
   document.querySelector('.file-icon-container').onclick=()=>window.iconClicks++;
   document.querySelector('.File').oncontextmenu=e=>{e.preventDefault();document.querySelector('.Menu').hidden=false};
   document.querySelector('button').onclick=e=>{
    if(!e.isTrusted) return;const a=document.createElement('a');a.download='sheet.xlsx';
    a.href=URL.createObjectURL(new Blob(['xlsx-test']));a.click();document.querySelector('.Menu').hidden=true;
   };
  }""")

        async def assert_selection(page):
            pass

        shared = SimpleNamespace(
            CAPTURE_DOWNLOADS=main.CAPTURE_DOWNLOADS,
            CAPTURE_STATUS=main.CAPTURE_STATUS,
            CAPTURE_CHUNK=main.CAPTURE_CHUNK,
            VISIBLE_CLICK_POINT=main.VISIBLE_CLICK_POINT,
            DownloadIncomplete=main.DownloadIncomplete,
            assert_selection=assert_selection,
        )
        item = {
            "id": "456",
            "token": "backup-0",
            "media": [{"kind": "document", "name": "sheet.xlsx", "count": 1}],
            "attachment_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "downloads"
            folder.mkdir()
            await adapters.download(self.page, item, folder, {"_checkpoint": lambda: None}, shared)
            self.assertEqual(next(folder.glob("*.xlsx")).read_bytes(), b"xlsx-test")
            self.assertEqual(await self.page.evaluate("window.iconClicks"), 0)

    async def test_telegram_blank_target_delayed_unicode_download(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        await self.page.set_content(
            '<div id="panel" data-backup-panel="selected" style="height:500px"><div class="Message" data-backup-token="backup-0"><div class="File"><div class="file-icon-container" style="width:80px;height:80px">download</div></div></div></div>'
        )
        await self.page.evaluate("""() => {
   window.__baleBackupBox=document.querySelector('#panel');window.clicks=0;
   document.querySelector('.file-icon-container').onclick=event=>{
    if(!event.isTrusted) return;window.clicks++;
    setTimeout(()=>{const link=document.createElement('a');
     link.href=URL.createObjectURL(new Blob([new Uint8Array(23000000)],{type:'application/octet-stream'}));
     link.target='_blank';link.rel='noopener noreferrer';link.download='رسپی (3).xlsx';link.click();
    },1000);
   };
  }""")

        async def assert_selection(page):
            pass

        shared = SimpleNamespace(
            CAPTURE_DOWNLOADS=main.CAPTURE_DOWNLOADS,
            CAPTURE_STATUS=main.CAPTURE_STATUS,
            CAPTURE_CHUNK=main.CAPTURE_CHUNK,
            VISIBLE_CLICK_POINT=main.VISIBLE_CLICK_POINT,
            DownloadIncomplete=main.DownloadIncomplete,
            assert_selection=assert_selection,
        )
        item = {
            "id": "789",
            "token": "backup-0",
            "media": [{"kind": "document", "name": "sheet.xlsx", "count": 1}],
            "attachment_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "downloads"
            folder.mkdir()
            await adapters.download(self.page, item, folder, {"_checkpoint": lambda: None}, shared)
            self.assertEqual(next(folder.glob("*.xlsx")).stat().st_size, 23000000)
            self.assertFalse(self.page.is_closed())
            self.assertEqual(len(self.page.context.pages), 1)
            self.assertEqual(await self.page.evaluate("window.clicks"), 1)


if __name__ == "__main__":
    unittest.main()
