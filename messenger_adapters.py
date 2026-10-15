"""DOM adapters. Download only through a messenger's own save action."""

import base64
import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from sender_extraction import wrap

PROVIDERS = {
    "bale": {
        "name": "Bale",
        "url": "https://web.bale.ai/",
        "selector": ".message-item, .message-block",
    },
    "telegram": {
        "name": "Telegram",
        "url": "https://web.telegram.org/a/",
        "selector": ".Message[data-message-id]",
    },
    "whatsapp": {
        "name": "WhatsApp",
        "url": "https://web.whatsapp.com/",
        "selector": '#main .message-in, #main .message-out, #main [data-testid^="conv-msg-"][data-id]:has([data-testid="msg-container"])',
    },
}

SCAN = r"""({selector,provider}) => {
 const root=window.__baleBackupBox;
 if(!root?.isConnected) throw new Error('Selected panel closed');
 const nodes=[...root.querySelectorAll(selector)].filter(e=>!e.parentElement?.closest(selector));
 function richText(node) {
   if(!node) return '';
   const copy=node.cloneNode(true);
   copy.querySelectorAll('img[alt]').forEach(e=>e.replaceWith(document.createTextNode(e.alt)));
   copy.querySelectorAll('.MessageMeta,.message-time,[data-testid="msg-meta"],.reactions,svg').forEach(e=>e.remove());
   return copy.textContent.trim();
 }
 return nodes.map((el,index)=>{
   const isTelegram=provider==='telegram';
   const clone=el.cloneNode(true);
   // Reply previews, web link previews, avatars and stickers are not attachments.
   clone.querySelectorAll('.embedded-message,.EmbeddedMessage,.WebPage,.web-page,.Avatar,.avatar,.Sticker,.sticker,[data-testid="quoted-message"],[data-testid="quoted-document"],[data-testid="quoted-media"],[data-testid="link-preview"],.quoted-mention').forEach(e=>e.remove());
   const pick=s=>clone.querySelector(s);
   let media=[];
   if(isTelegram) {
     const album=pick('.Album');
     const count=album ? album.querySelectorAll('.AlbumItem,.album-item,[data-message-id]').length : 0;
     const name=richText(pick('.File .file-name,.File .file-title,.Document .filename,.Document .file-name,.Document .title'));
     if(pick('.Location')) media=[]; // A map thumbnail opens a map chooser, not a photo.
     else if(album) media=[{kind:'album',name:'',count:Math.max(1,count),url:''}];
     else if(pick('.Audio,.message-content.voice,.message-content.audio')) media=[{kind:pick('.message-content.voice,.Audio.voice,.Audio.is-voice')?'voice':'audio',name,count:1,url:''}];
     else if(pick('.Document,.message-content.document .File')) media=[{kind:'document',name,count:1,url:''}];
     else if(pick('.Video,.RoundVideo,video,.message-content.video')) media=[{kind:'video',name:'',count:1,url:''}];
     else if(pick('.Photo,.message-content.media .media-inner:not(.file-preview)')) media=[{kind:'photo',name:'',count:1,url:''}];
   } else {
     const has=s=>Boolean(pick(s));
     let kind='';
     if(has('[data-testid="ptt-msg"],[data-icon="ptt"],[data-icon="ptt-play"],[data-testid="ptt-file"],[aria-label="Voice message"]')) kind='voice';
     else if(has('[data-testid="audio-player"],[data-icon="audio-play"],audio')) kind='audio';
     else if(has('[data-testid="document-thumb"],[data-testid="document-message"],[data-icon="document"],[data-icon="document-full"]')) kind='document';
     else if(has('video,[data-icon="video-play"],[data-testid="video-content"]')) kind='video';
     else if(has('img[src^="blob:"],img[data-testid="image-thumb"],[data-testid="image-content"]')) kind='photo';
     if(kind) media=[{kind,name:richText(pick('[data-testid="document-title"],[title]')),count:1,url:''}];
   }
   const meta=[...el.querySelectorAll('[data-pre-plain-text]')].find(e=>!e.closest('[data-testid="quoted-message"],[data-testid="quoted-document"],[data-testid="quoted-media"]'))?.getAttribute('data-pre-plain-text') || '';
   const parsed=meta.match(/^\[([^,]+),\s*([^\]]+)\]\s*(.*?):\s*$/);
   const stamp=el.getAttribute('data-timestamp') || el.querySelector('time')?.getAttribute('datetime');
   let date=parsed?.[2] || 'نامشخص', clock=parsed?.[1] || richText(el.querySelector('.message-time,.MessageMeta time,time,[data-testid="msg-meta"]')) || 'نامشخص';
   if(stamp) {
     const n=Number(stamp); const d=new Date(Number.isFinite(n)?(n<1e12?n*1000:n):stamp);
     if(!Number.isNaN(d.getTime())) {date=d.toLocaleDateString('fa-IR',{timeZone:'Asia/Tehran'});clock=d.toLocaleTimeString('fa-IR',{timeZone:'Asia/Tehran',hour:'2-digit',minute:'2-digit'});}
   }
   let date_source=parsed?'message_metadata':stamp?'timestamp':'unavailable';
   let date_raw=parsed?.[2] || stamp || '';
   if(isTelegram) {
     const ownTime=[...el.querySelectorAll('.MessageMeta .message-time')].find(e=>!e.closest('.EmbeddedMessage,.embedded-message'));
     const full=ownTime?.getAttribute('title')?.split('\n')[0]?.trim();
     if(full) {
       date=full; date_raw=full; date_source='telegram_original_time_tooltip';
       const match=full.match(/\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?/i);
       if(match) clock=match[0];
     } else {
       const group=el.closest('.message-date-group');
       const label=group?.querySelector(':scope > .sticky-date')?.textContent?.trim();
       if(label) {date=label+' [year not verified]';date_raw=label;date_source='telegram_day_header_partial';}
     }
   }
   const outgoing=el.matches('.message-out,.own') || Boolean(el.querySelector('[data-testid="tail-out"]'));
   const sender=parsed?.[3] || (outgoing?'من':richText(el.querySelector('.message-title,.sender-name')) || 'طرف مقابل');
   let text=isTelegram ? richText(clone.querySelector('.text-content,.message-text')) : [...clone.querySelectorAll('.selectable-text.copyable-text')].filter(e=>!e.parentElement?.closest('.selectable-text.copyable-text')).map(richText).join('\n');
   if(isTelegram && pick('.Location')) {
     const details=richText(pick('.Location'));
     text=[text,'[Location shared]',details].filter(Boolean).join('\n');
   }
   const id=el.getAttribute('data-message-id') || el.getAttribute('data-id') || el.querySelector('[data-id]')?.getAttribute('data-id') || '';
   const token='backup-'+index;el.setAttribute('data-backup-token',token);
   return {id,date,date_source,date_raw,sender,time:clock,text,media,token};
 });
}"""

SCAN = wrap(SCAN)

CHAT_IDENTITY = r"""provider => {
 const main=provider==='whatsapp'?document.querySelector('#main'):document.querySelector('#MiddleColumn');
 const header=main?.querySelector('header,.MiddleHeader,.middle-header');
 let peer=provider==='telegram' ? header?.querySelector('.ChatInfo [data-peer-id]')?.getAttribute('data-peer-id') || '' : '';
 if(provider==='whatsapp') {
   const jids=[...new Set([...main?.querySelectorAll('.message-in[data-id],.message-out[data-id],[data-testid^="conv-msg-"][data-id]') || []].map(e=>e.getAttribute('data-id')?.match(/^(?:true|false)_([^_]+@(?:c.us|g.us|s.whatsapp.net|lid))_/i)?.[1]).filter(Boolean))];
   if(jids.length===1) peer=jids[0];
 }
 const name=provider==='telegram' ? header?.querySelector('.ChatInfo .fullName,.ChatInfo h3,.ChatInfo .title') : header?.querySelector('[data-testid="conversation-info-header-chat-title"],span[title]');
 return {url:location.href,peer,title:name?.textContent?.trim() || '',panel:Boolean(window.__baleBackupBox?.isConnected)};
}"""


async def assert_chat(page, provider, error_type):
    current = await page.evaluate(CHAT_IDENTITY, provider)
    initial = getattr(page, "_backup_chat_identity", None)
    if not current["panel"] or (
        initial
        and (
            current["url"] != initial["url"]
            or current["peer"] != initial["peer"]
            or current["title"] != initial["title"]
        )
    ):
        raise error_type("Selected chat changed. Backup stopped.")


DOWNLOAD_LABEL = re.compile(
    r"^(?:Download(?: file)?|Save(?: file)?(?: as)?|Save to device|دانلود(?: فایل)?|بارگیری(?: فایل)?|ذخیره(?: فایل)?|ذخیره در دستگاه|تنزيل|تحميل)$",
    re.I,
)


async def activate_visible(page, target, shared):
    """Use a real mouse event at the visible intersection, without scrolling."""
    point = await target.evaluate(shared.VISIBLE_CLICK_POINT)
    bounds = await target.bounding_box()
    if point is None or bounds is None:
        return False
    x, y = bounds["x"] + point["x"], bounds["y"] + point["y"]
    hit = await target.evaluate(
        "(e,p)=>{const hit=document.elementFromPoint(p.x,p.y);return hit===e || e.contains(hit)}",
        {"x": x, "y": y},
    )
    if not hit:
        return False
    await page.mouse.click(x, y)
    return True


async def download(page, item, folder, cache, shared):
    if not item["media"]:
        return
    await shared.assert_selection(page)
    await dismiss_map_dialog(page)
    scope = page.locator('[data-backup-panel="selected"]')
    token = item.get("token", "")
    if not re.fullmatch(r"backup-\d+", token):
        raise shared.DownloadIncomplete("Invalid attachment location identifier")
    message = scope.locator(f'[data-backup-token="{token}"]')
    point = await message.evaluate(shared.VISIBLE_CLICK_POINT)
    if point is None:
        item["_media_deferred"] = True
        return
    item["_media_deferred"] = False
    await page.evaluate(shared.CAPTURE_DOWNLOADS)
    await page.evaluate(
        "() => {window.__backupCapture.files=[];window.__backupCapture.active=true;window.__backupCapture.native=false;}"
    )
    saved = []
    for ref in item.get("attachment_results", []):
        if not isinstance(ref, str) or not ref.startswith("downloads/"):
            continue
        existing = (folder.parent / ref).resolve()
        if (
            existing.is_relative_to(folder.resolve())
            and existing.is_file()
            and existing.stat().st_size > 0
        ):
            if ref not in saved:
                saved.append(ref)
    expected = sum(m.get("count", 1) for m in item["media"])
    if len(saved) >= expected:
        item["attachment_results"] = list(saved)
        await page.evaluate(
            "() => {window.__backupCapture.active=false;window.__backupCapture.files=[];}"
        )
        return

    def file_hash(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1048576), b""):
                digest.update(block)
        return digest.digest()

    def commit_partial(partial, target):
        content_hash = file_hash(partial)
        for ref in saved:
            existing = folder.parent / ref
            if (
                existing.stat().st_size == partial.stat().st_size
                and file_hash(existing) == content_hash
            ):
                partial.unlink()
                return False
        partial.replace(target)
        saved.append("downloads/" + target.name)
        item["attachment_results"] = list(saved)
        cache["_checkpoint"]()
        return True

    processed = set()
    requested = False
    complete = False
    viewer_opened = False
    logger = logging.getLogger("bale_backup")
    native_tasks = []

    async def persist_native(download, sequence):
        name = Path(download.suggested_filename.replace("\\", "/")).name
        name = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "_", name).strip(" .") or "attachment.bin"
        prefix = hashlib.sha256((item["id"] or item["_fingerprint"]).encode()).hexdigest()[:12]
        target = folder / f"{prefix}_{len(saved) + sequence:03d}_{name[:150]}"
        partial = target.with_name(target.name + ".part")
        await download.save_as(str(partial))
        failure = await download.failure()
        if failure or not partial.exists() or partial.stat().st_size == 0:
            raise shared.DownloadIncomplete(
                "Browser download failed: " + str(failure or "empty file")
            )
        if not commit_partial(partial, target):
            return
        logger.info(
            "Native file saved; id=%s; file=%s; bytes=%s",
            item["id"],
            target.name,
            target.stat().st_size,
        )

    def on_download(download):
        native_tasks.append(asyncio.create_task(persist_native(download, len(native_tasks) + 1)))

    page.on("download", on_download)

    async def click_download(scope):
        entries = scope.get_by_text(DOWNLOAD_LABEL, exact=True)
        for i in range(await entries.count()):
            entry = entries.nth(i)
            if await entry.is_visible() and await entry.evaluate(
                '(e)=>Boolean(e.closest("button,[role=button],[role=menuitem],.MenuItem,.ListItem"))'
            ):
                if await activate_visible(page, entry, shared):
                    return True
        # Click only known download icons, inside this message or media viewer.
        icons = scope.locator(
            '[data-icon="download"],[data-testid="download"],button[aria-label="Download"],button[title="Download"],button[aria-label="دانلود"],[aria-label="Download voice message"],[data-testid="document-thumb"][title^="Download "] ,.File .icon-download'
        )
        for i in range(await icons.count()):
            icon = icons.nth(i)
            if await icon.is_visible():
                if await activate_visible(page, icon, shared):
                    return True
        return False

    try:
        # Telegram's document transfer is handled by file-icon-container. Click
        # it once with native mouse input; a synthetic click may leave the
        # browser's user-activation-dependent save action waiting indefinitely.
        if item["media"][0]["kind"] == "document":
            # Prefer the explicit save action. Clicking File may only preload
            # its contents, or toggle/cancel an existing transfer.
            file = message.locator(
                ".File:not(.EmbeddedMessage .File):not(.embedded-message .File)"
            ).first
            file_point = (
                await file.evaluate(shared.VISIBLE_CLICK_POINT) if await file.count() else None
            )
            if file_point:
                bounds = await file.bounding_box()
                await page.mouse.click(
                    bounds["x"] + file_point["x"], bounds["y"] + file_point["y"], button="right"
                )
                await page.wait_for_timeout(400)
                menus = page.locator(
                    '[role="menu"],.Menu.open,.Menu.shown,.Menu .bubble.open,.bubble-menu,.context-menu'
                )
                requested = await click_download(menus)
                logger.info(
                    "Explicit document save menu activated: id=%s; found=%s", item["id"], requested
                )
                if not requested:
                    await dismiss_menu(page)
            controls = message.locator(".File .file-icon-container").filter(
                has_not=message.page.locator(".file-progress.shown,.file-progress.open")
            )
            for index in range(0 if requested else await controls.count()):
                control = controls.nth(index)
                if await control.is_visible():
                    requested = await activate_visible(page, control, shared)
                    if requested:
                        break
        if not requested:
            requested = await click_download(message)
        if not requested:
            await message.click(button="right", position=point, timeout=5000)
            await page.wait_for_timeout(400)
            menus = page.locator(
                '[role="menu"],.Menu.open,.Menu.shown,.Menu .bubble.open,.bubble-menu,.context-menu'
            )
            requested = await click_download(menus)
        if not requested and item["media"][0]["kind"] in ("photo", "video", "album"):
            # Dismiss the context menu using a click on the pinned chat's blank area.
            await dismiss_menu(page)
            media = message.locator(
                '.Photo,.Video,.album-item,.message-content.media .media-inner:not(.Location),[data-testid="image-content"],[data-testid="video-content"],img[src^="blob:"]:not(.Location img)'
            )
            if await media.count():
                await media.first.click(timeout=5000)
                viewer_opened = True
                await page.wait_for_timeout(500)
                requested = await click_download(
                    page.locator(
                        '#MediaViewer,.MediaViewer,[data-testid="media-viewer"],[role="dialog"]'
                    )
                )
        if not requested:
            raise shared.DownloadIncomplete(
                "Download action was not recognized. Backup stopped at this message for inspection."
            )
        expected = sum(m.get("count", 1) for m in item["media"])
        started = time.monotonic()
        deadline = started + 900
        next_notice = started + 15
        activity_seen = False
        label = item["media"][0].get("name") or item["media"][0].get("kind", "attachment")
        print(
            f"\nDownloading: {label} | message {item['id']} | maximum wait: 15 minutes", flush=True
        )
        while time.monotonic() < deadline:
            # Viewers may change URL temporarily; restore and validate after close.
            if not viewer_opened:
                await shared.assert_selection(page)
            if time.monotonic() >= next_notice:
                elapsed = int(time.monotonic() - started)
                print(
                    f"Waiting for download: {label} | elapsed {elapsed}s | saved {len(saved)}/{expected}",
                    flush=True,
                )
                logger.info(
                    "Download waiting: message=%s; elapsed=%s; saved=%s/%s",
                    item["id"],
                    elapsed,
                    len(saved),
                    expected,
                )
                next_notice = time.monotonic() + 15
            entries = await page.evaluate(shared.CAPTURE_STATUS)
            for task in native_tasks:
                if task.done():
                    task.result()
            activity_seen = activity_seen or bool(entries) or bool(native_tasks)
            if not activity_seen and time.monotonic() - started > 180:
                transferring = await message.locator(
                    ".file-progress:not(.hidden),.ProgressSpinner"
                ).count()
                if not transferring:
                    raise shared.DownloadIncomplete(
                        "No downloadable file received and no transfer indicator remains after 180 seconds. Saved data retained; no repeated click was sent."
                    )
            for entry in entries:
                if entry["id"] in processed:
                    continue
                if entry["error"]:
                    raise shared.DownloadIncomplete(entry["error"])
                if not entry["ready"]:
                    continue
                name = Path(entry["name"].replace("\\", "/")).name
                name = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "_", name).strip(" .") or "attachment.bin"
                prefix = hashlib.sha256((item["id"] or item["_fingerprint"]).encode()).hexdigest()[
                    :12
                ]
                target = folder / f"{prefix}_{len(saved) + 1:03d}_{name[:150]}"
                partial = target.with_name(target.name + ".part")
                with partial.open("wb") as output:
                    for offset in range(0, entry["size"], 196608):
                        data = await page.evaluate(
                            shared.CAPTURE_CHUNK,
                            {"id": entry["id"], "offset": offset, "length": 196608},
                        )
                        output.write(base64.b64decode(data, validate=True))
                if partial.stat().st_size != entry["size"] or entry["size"] == 0:
                    raise shared.DownloadIncomplete("Downloaded file is empty or incomplete")
                processed.add(entry["id"])
                if not commit_partial(partial, target):
                    continue
                logger.info(
                    "File saved; id=%s; file=%s; bytes=%s", item["id"], target.name, entry["size"]
                )
            if len(saved) >= expected:
                complete = True
                return
            await page.wait_for_timeout(500)
        raise shared.DownloadIncomplete(
            "Download did not finish within 15 minutes. The download request was not repeated."
        )
    except Exception as error:
        item["attachment_results"] = saved + [str(error)]
        raise
    finally:
        page.remove_listener("download", on_download)
        for task in native_tasks:
            if not task.done():
                task.cancel()
        if native_tasks:
            await asyncio.gather(*native_tasks, return_exceptions=True)
        if not page.is_closed():
            if viewer_opened:
                # Exact close action, never Escape which may also exit search.
                close = page.locator(
                    '#MediaViewer button[aria-label="Close"],.MediaViewer .close-button,[data-testid="media-viewer"] [data-icon="x"],[role="dialog"] [data-icon="x"]'
                )
                if await close.count():
                    await close.first.evaluate(
                        '(e)=>(e.closest("button,[role=button]")||e).click()'
                    )
            await dismiss_menu(page)
            await page.evaluate(
                "() => {window.__backupCapture.active=false;window.__backupCapture.native=false;window.__backupCapture.files=[];}"
            )
            if complete:
                await shared.assert_selection(page)


async def dismiss_menu(page):
    await page.evaluate(r"""() => {
      const panel=window.__baleBackupBox;
      if(panel?.isConnected) panel.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
    }""")


async def dismiss_map_dialog(page):
    """Close only Telegram's map-provider chooser, never unrelated dialogs."""
    dialogs = page.locator('dialog[open],[role="dialog"][aria-modal="true"]')
    for index in range(await dialogs.count()):
        dialog = dialogs.nth(index)
        if not await dialog.is_visible():
            continue
        if not await dialog.get_by_role("button", name="Bing Maps", exact=True).count():
            continue
        if not await dialog.get_by_role("button", name="Google Maps", exact=True).count():
            continue
        close = dialog.get_by_role("button", name="Close", exact=True)
        if not await close.count():
            close = dialog.get_by_role("button", name="Cancel", exact=True)
        if await close.count():
            original = await dialog.element_handle()
            await close.first.click(timeout=5000)
            await original.wait_for_element_state("hidden", timeout=5000)
            logging.getLogger("bale_backup").info(
                "Closed map-provider chooser before attachment download"
            )
            return
