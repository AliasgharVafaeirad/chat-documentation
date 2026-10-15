import json
import unittest
from playwright.async_api import async_playwright
import messenger_adapters as adapters
import main


class SenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_map_thumbnail_is_not_photo_and_dismiss_is_scoped(self):
        rows = await self.scan("""<div id="panel">
          <div class="Message" data-message-id="1"><div class="message-content media"><div class="Location media-inner"><img src="blob:map"></div></div></div>
          <div class="Message" data-message-id="2"><div class="Photo"></div></div>
          <div class="Message" data-message-id="3"><div class="Document"><span class="filename">file.docx</span></div></div>
        </div><dialog id="map"><button>Google Maps</button><button>Bing Maps</button><button aria-label="Close" onclick="this.closest('dialog').close()">Close</button></dialog>
        <div role="dialog" aria-modal="true" id="other"><button aria-label="Close">Close</button></div>""")
        self.assertEqual(rows[0]["media"], [])
        self.assertIn("[Location shared]", rows[0]["text"])
        self.assertEqual(rows[1]["media"][0]["kind"], "photo")
        self.assertEqual(rows[2]["media"][0]["kind"], "document")
        await self.page.locator("#map").evaluate("(e)=>e.showModal()")
        await adapters.dismiss_map_dialog(self.page)
        self.assertFalse(await self.page.locator("#map").is_visible())
        self.assertTrue(await self.page.locator("#other").is_visible())

    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        await self.page.route("**/*", lambda route: route.abort())
        await self.page.route(
            "https://web.telegram.org/a/**",
            lambda route: route.fulfill(status=200, body="<html></html>", content_type="text/html"),
        )
        await self.page.goto("https://web.telegram.org/a/")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def scan(self, html, provider="telegram"):
        await self.page.set_content(html)
        await self.page.evaluate("() => {window.__baleBackupBox=document.querySelector('#panel')}")
        return await self.page.evaluate(
            adapters.SCAN,
            {"selector": adapters.PROVIDERS[provider]["selector"], "provider": provider},
        )

    async def test_active_account_slot_and_secret_not_exported(self):
        await self.page.evaluate(
            "() => {localStorage.setItem('account1',JSON.stringify({userId:'1',firstName:'First'}));localStorage.setItem('account2',JSON.stringify({userId:'2',firstName:'Second',lastName:'User',authKey:'SECRET'}));}"
        )
        await self.page.goto("https://web.telegram.org/a/?account=2")
        rows = await self.scan(
            '<div id="panel"><div class="Message own" data-message-id="1"></div></div>'
        )
        self.assertEqual(rows[0]["sender"], "Second User")
        self.assertEqual(rows[0]["sender_id"], "2")
        account = await self.page.evaluate("window.__backupAccountIdentity")
        self.assertNotIn("SECRET", json.dumps(account))
        self.assertEqual(account["source"], "telegram_active_account")

    async def test_bale_sender_props_and_quote_are_separate(self):
        await self.page.set_content(
            '<div id="panel"><div class="message-item" data-sid="one"><div class="KTwPFW">hello</div><div data-sentry-component="Preview"><span class="sender-name">Quoted Person</span></div></div><div class="message-item" data-sid="two"><div data-sentry-component="Preview"><span class="sender-name">Other Quoted Person</span></div></div></div>'
        )
        await self.page.evaluate("""() => {
            window.__baleBackupBox=document.querySelector('#panel');
            document.querySelector('[data-sid="one"]')['__reactFiber$fixture']={memoizedProps:{messageContainer:{isOut:true,senderId:'7',sender:{id:'7',firstName:'Actual',lastName:'Sender'},message:{textMessage:{text:'hello'}}}},return:null};
        }""")
        rows = await self.page.evaluate(main.SCAN, main.SELECTOR)
        self.assertEqual(rows[0]["sender"], "Actual Sender")
        self.assertEqual(rows[0]["sender_id"], "7")
        self.assertEqual(rows[0]["sender_role"], "self")
        self.assertEqual(rows[1]["sender"], "نامشخص")

    async def test_bale_sender_uid_profile_requires_matching_identity(self):
        await self.page.set_content(
            '<div id="panel"><div class="message-item" data-sid="one"><span data-sentry-component="SenderName">Actual Name</span></div></div>'
        )
        await self.page.evaluate("""() => {
          window.__baleBackupBox=document.querySelector('#panel');
          const el=document.querySelector('.message-item');
          el['__reactFiber$fixture']={memoizedProps:{messageContainer:{isOut:false,senderUid:'42',message:{textMessage:{text:'hello'}}}},return:null};
          el.firstElementChild['__reactFiber$fixture']={memoizedProps:{user:{uid:'42',firstName:'Actual',lastName:'Name'}},return:null};
        }""")
        rows = await self.page.evaluate(main.SCAN, main.SELECTOR)
        self.assertEqual(rows[0]["sender_id"], "42")
        self.assertEqual(rows[0]["sender"], "Actual Name")
        self.assertEqual(rows[0]["sender_source"], "sender_uid_profile")

    async def test_fixed_legacy_name_is_ignored_and_account_switch_stops(self):
        await self.page.evaluate("() => {window.__backupSelfName='Previous user';}")
        rows = await self.scan(
            '<div id="panel"><div class="Message own" data-message-id="1"></div></div>'
        )
        self.assertEqual(rows[0]["sender"], "من")
        await self.page.evaluate(
            "() => localStorage.setItem('account1',JSON.stringify({userId:'1',firstName:'First'}))"
        )
        await self.scan('<div id="panel"><div class="Message own" data-message-id="1"></div></div>')
        await self.page.evaluate(
            "() => localStorage.setItem('account1',JSON.stringify({userId:'2',firstName:'Other'}))"
        )
        with self.assertRaisesRegex(Exception, "account changed"):
            await self.scan(
                '<div id="panel"><div class="Message own" data-message-id="1"></div></div>'
            )

    async def test_group_names_and_no_quote_forward_attribution(self):
        html = """<div id="MiddleColumn"><div class="MiddleHeader"><div class="ChatInfo"><div data-peer-id="-123"></div><h3 class="fullName">Group title</h3></div></div></div>
        <div id="panel">
        <div class="Message message-list-item first-in-group" data-message-id="1"><span class="sender-title">Alice</span><div class="text-content">hi</div></div>
        <div class="Message message-list-item last-in-group" data-message-id="2"><div class="EmbeddedMessage"><span class="sender-title">Bob quoted</span></div><div class="text-content">next</div></div>
        <div class="Message message-list-item first-in-group last-in-group" data-message-id="3"><span class="sender-title">Bob</span></div>
        <div class="Message message-list-item first-in-group last-in-group" data-message-id="4"><div class="message-title"><span class="forward-title">Forwarded from</span><span class="sender-title">Carol origin</span></div></div>
        <div class="Message message-list-item first-in-group last-in-group own" data-message-id="5"></div>
        </div>"""
        rows = await self.scan(html)
        self.assertEqual([r["sender"] for r in rows], ["Alice", "Alice", "Bob", "نامشخص", "من"])
        self.assertEqual(rows[1]["sender_source"], "message_group")
        self.assertEqual(rows[1]["sender_source_message_id"], "1")
        self.assertEqual(rows[3]["forwarded_from"], "Carol origin")
        self.assertEqual(rows[4]["sender_role"], "self")

    async def test_group_conflicting_sender_ids_not_carried(self):
        rows = await self.scan(
            '<div id="panel"><div class="Message message-list-item first-in-group" data-message-id="1" data-sender-id="10"><span class="sender-title">Alice</span></div><div class="Message message-list-item last-in-group" data-message-id="2" data-sender-id="20"></div><div class="Message message-list-item first-in-group last-in-group" data-message-id="3" data-sender-id="10"></div></div>'
        )
        self.assertEqual(rows[0]["sender"], "Alice")
        self.assertEqual(rows[1]["sender_status"], "unknown")
        self.assertEqual(rows[2]["sender"], "Alice")
        self.assertEqual(rows[2]["sender_source"], "explicit_sender_id")

    async def test_whatsapp_audio_is_not_automatically_a_voice(self):
        rows = await self.scan(
            '<div id="panel"><div id="main"><div class="message-in" data-id="1"><audio></audio></div><div class="message-in" data-id="2"><div data-testid="ptt-msg"></div></div></div></div>',
            "whatsapp",
        )
        self.assertEqual(rows[0]["media"][0]["kind"], "audio")
        self.assertEqual(rows[1]["media"][0]["kind"], "voice")

    async def test_whatsapp_metadata_of_each_participant_not_quoted_author(self):
        rows = await self.scan(
            """<div id="panel"><div id="main">
          <div class="message-in" data-id="1"><div data-pre-plain-text="[09:00, 10/7/2026] Alice: "><span class="selectable-text copyable-text">hi</span></div></div>
          <div class="message-in" data-id="2"><div data-testid="quoted-message" data-pre-plain-text="[08:00, 10/7/2026] Alice: "></div><div data-pre-plain-text="[09:01, 10/7/2026] Bob: "><span class="selectable-text copyable-text">hello</span></div></div>
          <div class="message-out" data-id="3"><div data-pre-plain-text="[09:02, 10/7/2026] Charlie: "></div></div>
        </div></div>""",
            "whatsapp",
        )
        self.assertEqual([r["sender"] for r in rows], ["Alice", "Bob", "Charlie"])
        self.assertEqual(rows[2]["sender_role"], "self")

    async def test_private_chat_forward_and_confirmed_self(self):
        await self.scan("""<div id="MiddleColumn"><div class="MiddleHeader"><div class="ChatInfo">
        <div data-peer-id="123"></div><h3 class="fullName">Ali</h3></div></div></div>
        <div id="panel"><div class="Message" data-message-id="1"><div class="message-title">
        <span class="forward-title">Forwarded from</span><span class="sender-title">Sara</span></div></div>
        <div class="Message own" data-message-id="2"><span class="sender-title">Wrong name</span></div></div>""")
        await self.page.evaluate(
            "() => {localStorage.setItem('account1',JSON.stringify({userId:'999',firstName:'Reza'}));}"
        )
        rows = await self.page.evaluate(
            adapters.SCAN,
            {"selector": adapters.PROVIDERS["telegram"]["selector"], "provider": "telegram"},
        )
        self.assertEqual(rows[0]["sender"], "Ali")
        self.assertEqual(rows[0]["forwarded_from"], "Sara")
        self.assertEqual(rows[1]["sender"], "Reza")

    async def test_bale_message_props_sender_and_merge_identity_enrichment(self):
        await self.page.set_content(
            '<div id="panel"><div class="message-item" data-sid="b1"><span class="KTwPFW">hello</span></div></div>'
        )
        await self.page.evaluate("""() => {
          window.__baleBackupBox=document.querySelector('#panel');
          document.querySelector('.message-item').__reactFiber$test={memoizedProps:{messageContainer:{isOut:false, senderId:'u2', sender:{id:'u2',firstName:'Group',lastName:'Member'},message:{textMessage:{text:'hello'}}}}};
        }""")
        rows = await self.page.evaluate(main.SCAN, ".message-item")
        self.assertEqual(rows[0]["sender"], "Group Member")
        self.assertEqual(rows[0]["sender_id"], "u2")
        self.assertEqual(rows[0]["sender_source"], "message_props")
        older = dict(rows[0], sender="نامشخص", sender_status="unknown", sender_source="unavailable")
        messages = {}
        previous = main.merge(messages, [older], [])
        main.merge(messages, rows, previous)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages["id:b1"]["sender"], "Group Member")


if __name__ == "__main__":
    unittest.main()
