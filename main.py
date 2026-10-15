"""Document a selected messenger conversation without sending or deleting messages."""

import argparse
import report_state
import shutil
import asyncio
import base64
import hashlib
import json
from app_config import load_config
import logging
import re
import sys
import messenger_adapters as adapters
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

BASE = Path(__file__).resolve().parent
BUILD = "chat-documentation-0.1.0"
SELECTOR = ".message-item, .message-block"
CAPTURE_DOWNLOADS = r"""() => {
 if (window.__backupCapture) return;
 const state={active:false, files:[], sequence:0};
 window.__backupCapture=state;
 const blobs=new Map();
 const create=URL.createObjectURL.bind(URL);
 URL.createObjectURL=function(blob) {
   const url=create(blob);
   if(blob instanceof Blob) blobs.set(url,new WeakRef(blob));
   return url;
 };
 function capture(anchor) {
   if(!state.active || state.native || !(anchor instanceof HTMLAnchorElement)) return false;
   if(!anchor.hasAttribute('download') && !anchor.href.startsWith('blob:')) return false;
   const entry={id:++state.sequence,name:anchor.download || 'attachment.bin',blob:null,error:null};
   state.files.push(entry);
   const blob=blobs.get(anchor.href)?.deref();
   if(blob) entry.blob=blob;
   else fetch(anchor.href,{credentials:'include'}).then(response=>{
     if(!response.ok) throw new Error('HTTP '+response.status);
     return response.blob();
   }).then(blob=>{entry.blob=blob;}).catch(error=>{entry.error=String(error);});
   return true;
 }
 const click=HTMLAnchorElement.prototype.click;
 HTMLAnchorElement.prototype.click=function() {
   if(capture(this)) return;
   return click.apply(this,arguments);
 };
 const dispatch=HTMLAnchorElement.prototype.dispatchEvent;
 HTMLAnchorElement.prototype.dispatchEvent=function(event) {
   if(event.type==='click' && capture(this)) return true;
   return dispatch.apply(this,arguments);
 };
 document.addEventListener('click',event=>{
   const anchor=event.target?.closest?.('a');
   if(capture(anchor)) {event.preventDefault();event.stopImmediatePropagation();}
 },true);
}"""
CAPTURE_STATUS = r"""() => (window.__backupCapture?.files || []).map(entry=>({
 id:entry.id,name:entry.name,error:entry.error,ready:Boolean(entry.blob),
 size:entry.blob?.size || 0,mime:entry.blob?.type || ''
}))"""
CAPTURE_CHUNK = r"""async ({id,offset,length}) => {
 const entry=window.__backupCapture.files.find(entry=>entry.id===id);
 if(!entry?.blob) throw new Error('Captured file unavailable');
 const bytes=new Uint8Array(await entry.blob.slice(offset,offset+length).arrayBuffer());
 let text='';
 for(let i=0;i<bytes.length;i+=8192) text+=String.fromCharCode(...bytes.subarray(i,i+8192));
 return btoa(text);
}"""
TRACK_PANEL = r"""() => {
 const remember = e => { window.__backupPointer = {x:e.clientX, y:e.clientY}; };
 document.addEventListener('pointermove', remember, true);
 document.addEventListener('pointerdown', remember, true);
}"""
PIN_PANEL = r"""selector => {
 const boxes = [];
 for (const message of document.querySelectorAll(selector)) {
   if (!message.getClientRects().length) continue;
   let box = message.parentElement;
   while (box && !(/auto|scroll/.test(getComputedStyle(box).overflowY) && box.clientHeight>0)) box=box.parentElement;
   if (box && !boxes.includes(box)) boxes.push(box);
 }
 let box = boxes.length===1 ? boxes[0] : null;
 const pointer = window.__backupPointer;
 if (!box && pointer) {
   const candidates = boxes.filter(e => {
     const r=e.getBoundingClientRect();
     return pointer.x>=r.left && pointer.x<=r.right && pointer.y>=r.top && pointer.y<=r.bottom;
   });
   candidates.sort((a,b)=>a.clientWidth*a.clientHeight-b.clientWidth*b.clientHeight);
   box = candidates[0];
 }
 if (!box) return {ok:false, count:boxes.length};
 window.__baleBackupBox = box;
 box.setAttribute('data-backup-panel', 'selected');
 return {ok:true, count:boxes.length};
}"""


class SelectionChanged(RuntimeError):
    pass


class DownloadIncomplete(RuntimeError):
    pass


VISIBLE_CLICK_POINT = r"""el => {
 const box=window.__baleBackupBox;
 const r=el.getBoundingClientRect();
 const b=box ? box.getBoundingClientRect() : {left:0,top:0,right:innerWidth,bottom:innerHeight};
 const left=Math.max(r.left,b.left,0),right=Math.min(r.right,b.right,innerWidth);
 const top=Math.max(r.top,b.top,0),bottom=Math.min(r.bottom,b.bottom,innerHeight);
 if(right-left<4 || bottom-top<4) return null;
 return {x:(left+right)/2-r.left,y:(top+bottom)/2-r.top};
}"""


async def confirm_download_warning(page):
    # Confirm only Bale's exact DownloadWarn modal, never another dialog.
    return await page.evaluate(r"""() => {
      const candidates=document.querySelectorAll('.bPF1pe, [data-sentry-component="DownloadWarnModalFC"], [data-sentry-source-file="DownloadWarn.modal.tsx"]');
      for(const el of candidates) {
        if(!el.getClientRects().length) continue;
        const key=Object.keys(el).find(k=>k.startsWith('__reactFiber$'));
        let fiber=key ? el[key] : null;
        for(let depth=0;fiber && depth<25;depth++,fiber=fiber.return) {
          const props=fiber.memoizedProps;
          if((props?.['data-sentry-component']==='DownloadWarnModalFC' ||
              props?.['data-sentry-source-file']==='DownloadWarn.modal.tsx') &&
             typeof props.onConfirm==='function') {
            props.onConfirm();return true;
          }
        }
      }
      return false;
    }""")


async def activate_download_entry(entry):
    # Bale's popup can extend past the viewport. Pointer clicks attempt to
    # scroll it into view (and can move the conversation). Activate only the
    # verified Download/Save entry through its DOM click event instead.
    await entry.evaluate(
        r"""el => {
      if(!el.isConnected || !el.closest('[aria-label="message-menu"]'))
        throw new Error('Download menu is no longer open');
      const text=el.textContent.trim();
      if(!/^(دانلود(?: فایل)?|بارگیری(?: فایل)?|Download(?: file)?|ذخیره(?:‌| )?(?:در|روی) (?:دستگاه|حافظه|گالری)|ذخیره فایل|Save to device|Save(?: file)?(?: as)?)$/i.test(text))
        throw new Error('Unexpected menu action');
      el.click();
    }""",
        timeout=5000,
    )


async def assert_selection(page):
    provider = getattr(page, "_backup_provider", "bale")
    if provider != "bale":
        await adapters.assert_chat(page, provider, SelectionChanged)
    selected_url = getattr(page, "_backup_selected_url", None)
    if selected_url is not None and page.url != selected_url:
        raise SelectionChanged("Selected page changed. Backup stopped to preserve the output.")
    if selected_url is not None and not await page.evaluate(
        "() => Boolean(window.__baleBackupBox?.isConnected)"
    ):
        raise SelectionChanged("Selected message panel closed or changed. Backup stopped.")


async def close_message_menu(page):
    # Escape also closes Bale search. Close only the message menu itself.
    return await page.evaluate(r"""() => {
      const menu=document.querySelector('[aria-label="message-menu"]');
      if (!menu) return true;
      const key=Object.keys(menu).find(k=>k.startsWith('__reactFiber$'));
      let fiber=key ? menu[key] : null;
      for(let depth=0;fiber && depth<25;depth++,fiber=fiber.return) {
        const close=fiber.memoizedProps?.handleContextMenuClose;
        if(typeof close==='function') { close(); return true; }
      }
      return false;
    }""")


SCAN = r"""selector => {
 const root = window.__baleBackupBox || document;
 if (root !== document && !root.isConnected) throw new Error('Selected panel closed');
 const nodes = [...root.querySelectorAll(selector)]
   .filter(e => !e.parentElement?.closest(selector));
 // Read only props belonging to this message. This avoids guessing a media
 // type from every img (emoji, avatars, play icons and previews are also imgs).
 function messageProps(el) {
   const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
   let fiber = key ? el[key] : null;
   for (let depth=0; fiber && depth<25; depth++, fiber=fiber.return) {
     const p = fiber.memoizedProps;
     if (p?.messageContainer?.message) return p.messageContainer;
     if (p?.messageContent?.message) return p.messageContent;
   }
   return null;
 }
 const componentSelector = '[data-testid="document-message"], ' +
   '[data-sentry-component="DocumentMessageFC"], ' +
   '[data-sentry-component="NewVoiceMessage"], ' +
   '[data-sentry-source-file="NewAudioMessage.tsx"], ' +
   '[data-sentry-component="PhotoMessage"], ' +
   '[data-sentry-component="VideoMessage"], ' +
   '[data-sentry-component="AlbumMessageFC"], [data-sentry-component="GifMessage"]';
 let date = "نامشخص";
 const result = [];
 for (const el of root.querySelectorAll(selector + ", .Wqgb2D")) {
   if (el.matches(".Wqgb2D")) {date = el.innerText.trim(); continue;}
   if (!nodes.includes(el)) continue;
   const text = el.querySelector(".KTwPFW");
   const time = el.querySelector(".HXOHbT, .bisANn, time");
   const incoming = el.closest(".hviPrH") || el.querySelector(".hviPrH");
   const props = messageProps(el);
   const content = props?.message;
   let kind = '';
   if (content?.albumMessage) kind = 'album';
   else if (content?.photoMessage) kind = 'photo';
   else if (content?.documentMessage) kind = 'document';
   else if (content?.voiceMessage) kind = 'voice';
   else if (content?.audioMessage) kind = 'audio';
   else if (content?.videoMessage) kind = 'video';
   if (!kind && el.querySelector(componentSelector)) kind = 'attachment';
   // Fallback recognizes actual audio/video elements, never generic img tags.
   if (!kind && el.querySelector('audio, video')) kind = 'attachment';
   const doc = content?.documentMessage || content?.voiceMessage ||
     content?.audioMessage || content?.videoMessage;
   if (kind==='document' && /^voice(?:_msg_-?\d+)?\.(?:ogg|opus|mp4)$/i.test(doc?.name || '')) kind='voice';
   const media = kind ? [{kind, name:doc?.name || '', url:'',
     count:content?.albumMessage?.mediaList?.length || 1}] : [];
   const rawDate = props?.date ?? el.getAttribute('data-date');
   let exactDate = date, exactTime = time ? time.innerText.trim() : 'نامشخص';
   if (rawDate && Number.isFinite(Number(rawDate)) && Number(rawDate)>0) {
     const stamp = Number(rawDate);
     const dt = new Date(stamp < 100000000000 ? stamp*1000 : stamp);
     exactDate = new Intl.DateTimeFormat('fa-IR', {timeZone:'Asia/Tehran'}).format(dt);
     exactTime = new Intl.DateTimeFormat('fa-IR', {timeZone:'Asia/Tehran',hour:'2-digit',minute:'2-digit',hour12:false}).format(dt);
   }
   // innerText omits emoji rendered as images. Preserve their alt text.
   function readableText(node) {
     if (!node) return '';
     const clone = node.cloneNode(true);
     for (const image of clone.querySelectorAll('img')) {
       const url = image.getAttribute('src') || '';
       image.replaceWith(document.createTextNode(/emoji-data|emoji/i.test(url + ' ' + image.className) ? image.alt || '' : ''));
     }
     for (const br of clone.querySelectorAll('br')) br.replaceWith('\n');
     return clone.textContent.trim();
   }
   const plain = content?.textMessage?.text;
   const caption = doc?.caption?.text || content?.photoMessage?.caption?.text;
   const sid = el.getAttribute('data-sid') || el.querySelector('[data-sid]')?.getAttribute('data-sid') || '';
   const token = 'backup-' + result.length;
   el.setAttribute('data-backup-token', token);
   result.push({
     id:sid || el.closest("[data-message-id]")?.getAttribute("data-message-id") || el.closest("[data-mid]")?.getAttribute("data-mid") || "",
     date:exactDate, sender:props ? (props.isOut ? 'من' : 'مخاطب') : (incoming ? 'من' : 'مخاطب'),
     time:exactTime,
     text:plain ?? caption ?? (text ? readableText(text) : readableText(el)),
     media, token
   });
 }
 return result;
}"""
SCAN = adapters.wrap(SCAN, "bale")

SCROLL = r"""selector => {
 const root = window.__baleBackupBox || document;
 if (root !== document && !root.isConnected) throw new Error('Selected panel closed');
 const message = root.querySelector(selector);
 if (!message) throw new Error("Message container not found");
 let box = window.__baleBackupBox || message.parentElement;
 while (!window.__baleBackupBox && box && !(box.scrollHeight > box.clientHeight + 5 &&
                 /auto|scroll/.test(getComputedStyle(box).overflowY))) box = box.parentElement;
 if (!box) throw new Error("Scrollable conversation not found");
 const before = box.scrollTop;
 box.scrollTop += Math.max(150, box.clientHeight * 0.65);
 return {before, after:box.scrollTop, bottom:box.scrollTop + box.clientHeight >= box.scrollHeight - 5};
}"""


def fingerprint(message):
    # Media URLs may be temporary, so exclude them from text identity.
    data = [
        message["date"],
        message["sender"],
        message["time"],
        message["text"],
        [(m["kind"], m["name"]) for m in message["media"]],
    ]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def merge(messages, batch, previous, provider=None):
    old_order = list(messages)
    keys = []
    occurrence = {}
    for item in batch:
        digest = fingerprint(item)
        occurrence[digest] = occurrence.get(digest, 0) + 1
        key = "id:" + item["id"] if item["id"] else None
        # Match the overlap between consecutive windows, preserving identical
        # messages at different positions when the DOM supplies no message ID.
        if key is None:
            candidates = [
                k for k in previous if messages[k]["_fingerprint"] == digest and k not in keys
            ]
            if candidates:
                key = candidates[0]
            else:
                key = f"fallback:{digest}:{len(messages)}:{occurrence[digest]}"
        if key not in messages:
            messages[key] = dict(
                item,
                _fingerprint=digest,
                attachment_results=[],
                _voice_task_prefix="m" + hashlib.sha256(key.encode()).hexdigest()[:16],
            )
        else:
            messages[key]["token"] = item.get("token", "")
            messages[key]["text"] = item.get("text", messages[key].get("text", ""))
            messages[key]["_fingerprint"] = digest
            if (
                item.get("date_source") == "telegram_original_time_tooltip"
                or messages[key].get("date") == "نامشخص"
            ):
                for field in ("date", "date_source", "date_raw", "time"):
                    if field in item:
                        messages[key][field] = item[field]
            if (
                item.get("sender_status") == "displayed_name"
                or not messages[key].get("sender_source")
                or (
                    item.get("sender_role") == "self"
                    and messages[key].get("sender_status") == "unknown"
                )
            ):
                for field in (
                    "sender",
                    "sender_id",
                    "sender_role",
                    "sender_source",
                    "sender_status",
                    "sender_source_message_id",
                    "forwarded_from",
                ):
                    if field in item:
                        messages[key][field] = item[field]
            if not messages[key]["media"] and item["media"]:
                messages[key]["_media_done"] = False
            messages[key]["media"] = item["media"]
        keys.append(key)
    ordered = report_state.ordered_keys(old_order, keys)
    if provider == "telegram" and all(re.fullmatch(r"id:\d+", k) for k in ordered):
        ordered.sort(key=lambda k: int(k[3:]))
    reordered = {key: messages[key] for key in ordered}
    messages.clear()
    messages.update(reordered)
    return keys


def safe_cell(value):
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def save(messages, folder):
    payload = list(messages.values())
    temporary = folder / "messages.tmp.json"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(folder / "messages.json")
    rows = []
    for number, m in enumerate(payload, 1):
        text = m["text"]
        # Excel limits each cell to 32767 characters; JSON retains full text.
        for part in range(max(1, (len(text) + 31999) // 32000)):
            rows.append(
                {
                    "ردیف": number,
                    "بخش متن": part + 1,
                    "تاریخ": safe_cell(m["date"]),
                    "فرستنده": safe_cell(m["sender"]),
                    "ساعت": safe_cell(m["time"]),
                    "متن پیام": safe_cell(text[part * 32000 : (part + 1) * 32000]),
                    "ضمیمه": safe_cell("\n".join(m["attachment_results"])),
                }
            )
    try:
        target = (
            folder / "خروجی_کامل_چت_بله.xlsx"
            if folder.parent.name == "bale_backups"
            else folder / "خروجی_چت.xlsx"
        )
        temporary = folder / "export.tmp.xlsx"
        pd.DataFrame(rows).to_excel(temporary, index=False, engine="openpyxl")
        temporary.replace(target)
        return True
    except Exception as error:
        print(f"\nCould not save Excel; the full JSON is preserved: {error}")
        return False


async def download_media(page, item, folder, cache):
    """Capture Bale's download payload and save it without Chrome downloads."""
    if not item["media"]:
        return
    await assert_selection(page)
    token = item.get("token", "")
    if not re.fullmatch(r"backup-\d+", token):
        item["attachment_results"] = ["Attachment could not be located on the page"]
        return
    scope = (
        page.locator('[data-backup-panel="selected"]')
        if getattr(page, "_backup_selected_url", None)
        else page
    )
    message = scope.locator(f'[data-backup-token="{token}"]')
    bubble = message.locator(".message-block").first
    if not await bubble.count():
        bubble = message
    click_point = await bubble.evaluate(VISIBLE_CLICK_POINT)
    if click_point is None:
        item["_media_deferred"] = True
        return
    item["_media_deferred"] = False
    download_label = re.compile(
        r"^(?:دانلود(?: فایل)?|بارگیری(?: فایل)?|Download(?: file)?)$", re.I
    )
    save_label = re.compile(
        r"^(?:ذخیره(?:‌| )?(?:در|روی) (?:دستگاه|حافظه|گالری)|ذخیره فایل|Save to device|Save(?: file)?(?: as)?)$",
        re.I,
    )
    saved, processed = [], set()
    completed = False
    requested = False
    logger = logging.getLogger("bale_backup")
    await page.evaluate(CAPTURE_DOWNLOADS)
    await page.evaluate(
        "() => {window.__backupCapture.files=[];window.__backupCapture.active=true;}"
    )
    try:

        async def open_menu():
            await assert_selection(page)
            if not await close_message_menu(page):
                raise RuntimeError("Could not close the message menu without leaving search")
            point = await bubble.evaluate(VISIBLE_CLICK_POINT)
            if point is None:
                raise RuntimeError("Attachment message is outside the visible panel")
            if item["media"][0]["kind"] == "album":
                # A physical click targets a tile's data-media-key and opens
                # that single photo's menu. Bubble on the album container so
                # Bale resolves its data-sid and the whole album instead.
                rect = await bubble.bounding_box()
                await bubble.dispatch_event(
                    "contextmenu",
                    {
                        "button": 2,
                        "buttons": 2,
                        "bubbles": True,
                        "cancelable": True,
                        "clientX": rect["x"] + point["x"],
                        "clientY": rect["y"] + point["y"],
                    },
                )
                logger.info("Opened whole-album menu; id=%s", item["id"])
            else:
                await bubble.click(button="right", position=point, timeout=5000)
            await page.wait_for_timeout(350)
            await assert_selection(page)
            menu = page.locator('[aria-label="message-menu"]')
            for label in (save_label, download_label):
                entries = menu.get_by_text(label, exact=True)
                for index in range(await entries.count()):
                    entry = entries.nth(index)
                    if await entry.is_visible():
                        return entry, label is save_label
            return None, False

        async def persist(entry, sequence):
            suggested = Path(entry["name"].replace("\\", "/")).name
            suggested = (
                re.sub(r'[\\/*?:"<>|\x00-\x1f]', "_", suggested).strip(" .") or "attachment.bin"
            )
            prefix = hashlib.sha256((item["id"] or item["_fingerprint"]).encode()).hexdigest()[:12]
            path = folder / f"{prefix}_{sequence:03d}_{suggested[:150]}"
            partial = path.with_name(path.name + ".part")
            try:
                with partial.open("wb") as output:
                    for offset in range(0, entry["size"], 196608):
                        await assert_selection(page)
                        data = await page.evaluate(
                            CAPTURE_CHUNK, {"id": entry["id"], "offset": offset, "length": 196608}
                        )
                        output.write(base64.b64decode(data, validate=True))
                if partial.stat().st_size != entry["size"]:
                    raise RuntimeError("Saved file size does not match the downloaded file")
                partial.replace(path)
                saved.append(f"downloads/{path.name}")
                item["attachment_results"] = list(saved)
                logger.info(
                    "File saved; id=%s; file=%s; bytes=%s", item["id"], path.name, entry["size"]
                )
                checkpoint = cache.get("_checkpoint")
                if checkpoint:
                    checkpoint()
            except SelectionChanged:
                raise
            except Exception as error:
                saved.append(f"Failed to save attachment: {error}")

        print(
            f"\nDownloading attachment: {item['media'][0]['name'] or item['media'][0]['kind']}",
            flush=True,
        )
        entry, ready = await open_menu()
        if entry is None:
            raise RuntimeError("Download/Save action was not found for this message")
        save_requested = ready
        requested = True
        logger.info("Single download action; id=%s; save=%s", item["id"], ready)
        await activate_download_entry(entry)
        await assert_selection(page)
        expected = item["media"][0].get("count", 1)
        deadline = asyncio.get_running_loop().time() + 900
        next_menu = asyncio.get_running_loop().time() + 3
        next_notice = asyncio.get_running_loop().time() + 15
        stable_since = None
        while asyncio.get_running_loop().time() < deadline:
            await assert_selection(page)
            if await confirm_download_warning(page):
                save_requested = True
                logger.info("DownloadWarn confirmed once; id=%s", item["id"])
            entries = await page.evaluate(CAPTURE_STATUS)
            for entry in entries:
                if entry["id"] in processed:
                    continue
                if entry["error"]:
                    saved.append(f"Attachment download failed: {entry['error']}")
                    processed.add(entry["id"])
                elif entry["ready"]:
                    await persist(entry, len(processed) + 1)
                    processed.add(entry["id"])
                    print(f"Attachments saved: {len(processed)}/{expected}", flush=True)
            now = asyncio.get_running_loop().time()
            if len(processed) >= expected and all(e["id"] in processed for e in entries):
                stable_since = stable_since or now
                if now - stable_since >= 3:
                    break
            else:
                stable_since = None
            # The first action may only fetch into Bale's cache. Only press
            # Save once a payload has not yet been captured.
            if not entries and not save_requested and now >= next_menu:
                entry, ready = await open_menu()
                if entry is not None and ready:
                    # Set before clicking: late Blob creation must never cause
                    # another Save action for the same in-flight download.
                    save_requested = True
                    logger.info("Single save action after cache load; id=%s", item["id"])
                    await activate_download_entry(entry)
                    await assert_selection(page)
                else:
                    await close_message_menu(page)
                next_menu = now + 3
            if now >= next_notice:
                print(
                    f"Waiting for remaining files: {len(processed)}/{expected} received", flush=True
                )
                logger.info(
                    "Waiting for attachment files; id=%s; received=%s; expected=%s",
                    item["id"],
                    len(processed),
                    expected,
                )
                next_notice = now + 15
            await asyncio.sleep(0.5)
        if len(processed) < expected:
            raise DownloadIncomplete(
                f"Download of {item['media'][0]['name'] or 'attachment'} is incomplete. Backup stopped here; the download request will not be repeated."
            )
        item["attachment_results"] = saved
        completed = True
        if not all(value.startswith("downloads/") for value in saved):
            raise DownloadIncomplete("Attachment save failed. Backup stopped here.")
    except SelectionChanged:
        item["attachment_results"] = saved + ["Page changed while downloading the attachment"]
        raise
    except DownloadIncomplete as error:
        item["attachment_results"] = saved + [str(error)]
        raise
    except Exception as error:
        item["attachment_results"] = saved + [f"Attachment was not downloaded: {error}"]
        print(f"Attachment was not downloaded: {error}", flush=True)
        try:
            diagnostic = folder.parent / "attachment_diagnostics"
            diagnostic.mkdir(exist_ok=True)
            name = hashlib.sha256((item["id"] or item["_fingerprint"]).encode()).hexdigest()[:16]
            (diagnostic / f"{name}.html").write_text(
                await message.evaluate("(e)=>e.outerHTML"), encoding="utf-8"
            )
        except Exception:
            pass
        if requested:
            raise DownloadIncomplete(f"Attachment download stopped: {error}") from error
    finally:
        if not page.is_closed():
            # Keep an unfinished capture armed: a late save must not fall back
            # into Chrome's native downloader after the extraction is paused.
            if completed or not requested:
                await page.evaluate(
                    "() => {window.__backupCapture.active=false;window.__backupCapture.files=[];}"
                )
            await close_message_menu(page)


async def run(selector=None, provider="bale", process_voices=True, resume_report=None):
    config = adapters.PROVIDERS[provider]
    selector = selector or config["selector"]
    folder = (
        Path(resume_report).resolve()
        if resume_report
        else BASE / f"{provider}_backups" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    )
    if resume_report and (
        folder.parent != (BASE / f"{provider}_backups").resolve()
        or not (folder / "messages.json").is_file()
    ):
        raise ValueError("Select a saved report belonging to this messenger in this project.")
    attempt = folder / "attempts" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    attempt.mkdir(parents=True, exist_ok=True)
    downloads = folder / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bale_backup")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(folder / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    attempt_handler = logging.FileHandler(attempt / "run.log", encoding="utf8")
    attempt_handler.setFormatter(handler.formatter)
    logger.addHandler(attempt_handler)
    logger.info("Backup started; build=%s; source=%s", BUILD, Path(__file__).resolve())
    print(f"Program version: {BUILD}\nRunning file: {Path(__file__).resolve()}", flush=True)
    messages, previous, cache = {}, [], {}
    cache["_checkpoint"] = lambda: save(messages, folder)
    excel_ok = False
    async with async_playwright() as p:
        bundled = Path(p.chromium.executable_path).is_file()
        launch_options = dict(
            headless=False, accept_downloads=True, downloads_path=str(folder / "browser_downloads")
        )
        if not bundled:
            launch_options["channel"] = "chrome"
        profile = BASE / (
            f"{provider}_automation_user_data" if bundled else f"{provider}_user_data"
        )
        print("Browser: bundled Chromium" if bundled else "Browser: installed Chrome", flush=True)
        while True:
            try:
                context = await p.chromium.launch_persistent_context(str(profile), **launch_options)
                break
            except Exception as error:
                if "existing browser session" not in str(
                    error
                ) and "profile is already in use" not in str(error):
                    raise
                print(
                    "The previous backup browser is still open. Close that window first.",
                    flush=True,
                )
                answer = input("Press Enter to retry, or type cancel: ").strip().lower()
                if answer == "cancel":
                    return
        browser_closed = asyncio.Event()
        context.on("close", lambda _: browser_closed.set())
        stop_reason = ""
        extraction_complete = False
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("crash", lambda _: logger.error("Browser page crashed"))
            page.on(
                "download",
                lambda d: logger.info("Browser download event: name=%s", d.suggested_filename),
            )
            context.on("close", lambda _: logger.warning("Browser context closed"))
            await page.add_init_script("(" + CAPTURE_DOWNLOADS + ")()")
            await page.add_init_script("(" + TRACK_PANEL + ")()")
            await page.evaluate(CAPTURE_DOWNLOADS)
            page.on("close", lambda _: logger.warning("Page closed"))
            page.on("crash", lambda _: logger.error("Page crashed"))
            if not page.url.startswith(config["url"]):
                await page.goto(config["url"], wait_until="domcontentloaded", timeout=60000)
            await page.evaluate(TRACK_PANEL)
            print(f"Log in to {config['name']} and open the desired chat.")
            if provider != "bale":
                print(
                    "New messenger support: first test a short chat with text, photos, documents and voice notes."
                )
            print(
                "Go to the starting point and place the mouse pointer over the message panel to back up."
            )
            print("Do not switch chats during backup. Press Ctrl+C to stop.")
            await asyncio.to_thread(input, "When ready, press Enter: ")
            account_script = (BASE / "account_identity.js").read_text(encoding="utf8")
            account = await page.evaluate(account_script, provider)
            account_path = folder / "account_identity.json"
            if account_path.exists():
                previous_account = json.loads(account_path.read_text(encoding="utf8"))
                if (
                    previous_account.get("id")
                    and account.get("id")
                    and previous_account["id"] != account["id"]
                ):
                    raise RuntimeError(
                        "This report belongs to another messenger account. Start a separate report."
                    )
            report_state.write(account_path, account)
            await page.evaluate("(identity) => {window.__backupAccountIdentity=identity;}", account)
            print(
                "Sender identity: "
                + (
                    "active messenger account detected"
                    if account.get("name")
                    else "using message metadata; unresolved identities remain marked"
                ),
                flush=True,
            )
            await page.wait_for_timeout(1500)
            if not await page.locator(selector).count():
                (folder / "structure.html").write_text(await page.content(), encoding="utf-8")
                raise RuntimeError(f"Message structure was not recognized. Local report: {folder}")
            pinned = await page.evaluate(PIN_PANEL, selector)
            if not pinned["ok"]:
                raise RuntimeError(
                    "Message panel is ambiguous. Place the pointer over the desired messages and run again."
                )
            page._backup_selected_url = page.url
            page._backup_provider = provider
            if provider != "bale":
                page._backup_chat_identity = await page.evaluate(adapters.CHAT_IDENTITY, provider)
            identity = await page.evaluate(adapters.CHAT_IDENTITY, provider)
            report_state.bind(folder, provider, identity, bool(resume_report))
            if resume_report:
                shutil.copy2(folder / "messages.json", attempt / "messages_before.json")
                messages.update(report_state.load_messages(folder))
                previous = list(messages)
                print(
                    f"Completing selected report: {folder} | saved messages: {len(messages)}",
                    flush=True,
                )
            print(f"Backing up the selected page: {page.url}")
            logger.info("Selected page: %s; panels: %s", page.url, pinned)
            quiet = 0
            stalled = 0
            empty = 0
            while quiet < 15:
                await assert_selection(page)
                if provider == "telegram":
                    await page.evaluate("""() => {
                        const root=window.__baleBackupBox;
                        root?.querySelectorAll('.Message .MessageMeta .message-time').forEach(e=>{
                            e.dispatchEvent(new MouseEvent('mouseenter', {bubbles:false}));
                            e.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
                        });
                    }""")
                    await page.wait_for_timeout(100)
                batch = (
                    await page.evaluate(SCAN, selector)
                    if provider == "bale"
                    else await page.evaluate(
                        adapters.SCAN, {"selector": selector, "provider": provider}
                    )
                )
                if not batch:
                    empty += 1
                    logger.warning("Messages temporarily absent (%s)", empty)
                    if empty >= 15:
                        raise RuntimeError(
                            "Messages did not load in the selected panel. See run.log."
                        )
                    await page.wait_for_timeout(1500)
                    continue
                empty = 0
                if provider != "bale":
                    meaningful = any(x["text"] or x["media"] for x in batch)
                    if not meaningful:
                        raise RuntimeError(
                            "Unsupported message structure. Empty messages were not treated as a successful backup."
                        )
                before = len(messages)
                if not resume_report and before == 0:
                    participants = [
                        row
                        for row in batch
                        if row.get("sender_role") == "participant"
                        and (row.get("text") or row.get("media"))
                    ]
                    if len(participants) >= 2 and not any(
                        row.get("sender_status") == "displayed_name" for row in participants
                    ):
                        report_state.write(
                            folder / "sender_preflight.json",
                            {
                                "status": "failed",
                                "reason": "Incoming sender identities unavailable",
                                "samples": [
                                    {
                                        key: row.get(key, "")
                                        for key in (
                                            "id",
                                            "sender",
                                            "sender_id",
                                            "sender_role",
                                            "sender_source",
                                        )
                                    }
                                    for row in participants[:8]
                                ],
                            },
                        )
                        (folder / "sender_structure.html").write_text(
                            await page.content(), encoding="utf8"
                        )
                        raise RuntimeError(
                            "Sender verification failed before downloads. Incoming authors are unknown. Report diagnostics saved; do not use this capture as a documented group report."
                        )
                previous = merge(messages, batch, previous, provider)
                # Save text before downloading: a failed download must not lose it.
                if len(messages) > before:
                    excel_ok = save(messages, folder)
                media_changed = False
                for key in dict.fromkeys(previous):
                    item = messages[key]
                    if not item.get("_media_done") and item.get("_media_attempts", 0) < 1:
                        logger.info("Attachment start: id=%s; type=%s", item["id"], item["media"])
                        try:
                            if provider == "bale":
                                await download_media(page, item, downloads, cache)
                            else:
                                await adapters.download(
                                    page, item, downloads, cache, sys.modules[__name__]
                                )
                        except DownloadIncomplete as error:
                            # A single attachment failure must not discard later
                            # messages. Chat changes and browser loss still stop.
                            await assert_selection(page)
                            logger.warning(
                                "Attachment incomplete; continuing: id=%s; error=%s",
                                item["id"],
                                error,
                            )
                            item["attachment_results"] = [
                                v
                                for v in item.get("attachment_results", [])
                                if v.startswith("downloads/")
                            ] + [str(error)]
                            item["_media_deferred"] = False
                            print(
                                f"\nAttachment incomplete: message {item['id']}. Continuing chat extraction.",
                                flush=True,
                            )
                            save(messages, folder)
                        if item.get("_media_deferred"):
                            continue
                        logger.info("Attachment result: %s", item["attachment_results"])
                        item["_media_attempts"] = item.get("_media_attempts", 0) + 1
                        item["_media_done"] = not item["media"] or (
                            bool(item["attachment_results"])
                            and all(
                                value.startswith("downloads/")
                                for value in item["attachment_results"]
                            )
                        )
                        media_changed = media_changed or bool(item["media"])
                if len(messages) > before or media_changed:
                    excel_ok = save(messages, folder)
                await assert_selection(page)
                status = await page.evaluate(SCROLL, selector)
                logger.info(
                    "messages=%s; new=%s; scroll=%s; quiet=%s",
                    len(messages),
                    len(messages) - before,
                    status,
                    quiet,
                )
                quiet = quiet + 1 if len(messages) == before and status["bottom"] else 0
                if not status["bottom"] and status["before"] == status["after"]:
                    stalled += 1
                    if stalled >= 15:
                        raise RuntimeError(
                            "Scrolling remained stuck after several waits. See run.log."
                        )
                    print(
                        "\nScrolling is temporarily stuck. Waiting for messages to load...",
                        flush=True,
                    )
                    await page.wait_for_timeout(1500)
                else:
                    stalled = 0
                print(
                    f"\rMessages saved: {len(messages)} | End-of-panel checks: {quiet}/15",
                    end="",
                    flush=True,
                )
                await page.wait_for_timeout(1200)
            extraction_complete = True
            stop_reason = "No new messages appeared at the end of the selected section for 18 seconds. Full-chat completeness is not verified."
            print(f"\nFinished: {stop_reason}")
            logger.info("Stopped: %s", stop_reason)
        except asyncio.CancelledError:
            stop_reason = "Stopped manually"
            raise
        except Exception as error:
            stop_reason = str(error)
            logger.exception("Extraction stopped")
            print(f"\nBackup stopped: {error}", flush=True)
            try:
                if not page.is_closed():
                    (folder / "structure_at_stop.html").write_text(
                        await page.content(), encoding="utf-8"
                    )
                    await page.screenshot(path=str(folder / "screen_at_stop.png"))
            except Exception:
                logger.exception("Could not capture page at stop")
        finally:
            (attempt / "stop_reason.txt").write_text(
                stop_reason or "Unknown stop reason; check run.log", encoding="utf8"
            )
            if messages:
                (folder / "stop_reason.txt").write_text(
                    stop_reason or "Unknown stop reason; check run.log", encoding="utf-8"
                )
                metadata_path = folder / "report.json"
                if metadata_path.exists():
                    metadata = json.loads(metadata_path.read_text(encoding="utf8"))
                    metadata.update(
                        source_kind="browser_capture",
                        capture_state="section_complete" if extraction_complete else "interrupted",
                    )
                    report_state.write(metadata_path, metadata)
            if messages:
                excel_ok = save(messages, folder)
                print(f"\nOutput: {folder}")
                print("Excel saved." if excel_ok else "Excel was not saved; keep messages.json.")
                saved_files = sum(
                    value.startswith("downloads/")
                    for item in messages.values()
                    for value in item["attachment_results"]
                )
                missing = sum(
                    bool(item["media"]) and not item.get("_media_done", False)
                    for item in messages.values()
                )
                print(
                    f"Files saved: {saved_files} | Messages with incomplete attachments: {missing}"
                )
            print(f"Run log: {folder / 'run.log'}", flush=True)
            if (
                messages
                and process_voices
                and extraction_complete
                and not asyncio.current_task().cancelling()
            ):
                print("Starting voice processing and final Excel export...", flush=True)
                import subprocess

                config_path = BASE / "pipeline_config.json"
                try:
                    pipeline_config = load_config(BASE)
                    code = await asyncio.to_thread(
                        subprocess.call,
                        [
                            pipeline_config["voice_python"],
                            "-u",
                            str(BASE / "process_backup.py"),
                            "--backup",
                            str(folder),
                        ],
                    )
                    if code:
                        print(
                            "Voice processing did not finish. Completed work is saved. Use run_process_backup.cmd to resume.",
                            flush=True,
                        )
                except Exception as error:
                    print(f"Voice processing could not start: {error}", flush=True)
            if not browser_closed.is_set() and not asyncio.current_task().cancelling():
                print(
                    "Browser stays open. Close the browser window yourself to exit the program.",
                    flush=True,
                )
                await browser_closed.wait()
            await context.close()
            logger.removeHandler(handler)
            handler.close()
            logger.removeHandler(attempt_handler)
            attempt_handler.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--message-selector", default=None)
    parser.add_argument("--messenger", choices=tuple(adapters.PROVIDERS))
    parser.add_argument(
        "--transcribe-voices",
        action="store_true",
        help="Transcribe voice files in an existing backup locally",
    )
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--skip-voice-processing", action="store_true", help="Download only")
    args = parser.parse_args()
    if args.transcribe_voices:
        import subprocess

        voice_python = Path(load_config(BASE)["voice_python"])
        if not voice_python.is_file():
            print(
                "Voice transcription environment is missing. Use run_voice_transcription.cmd after setup."
            )
            raise SystemExit(1)
        raise SystemExit(
            subprocess.call([str(voice_python), "-u", str(BASE / "transcribe_voices.py")])
        )
    try:
        provider = args.messenger
        if args.resume_report:
            inferred = args.resume_report.resolve().parent.name.removesuffix("_backups")
            if inferred not in adapters.PROVIDERS:
                raise ValueError("Invalid report messenger")
            if provider and provider != inferred:
                raise ValueError("Report messenger mismatch")
            provider = inferred
        if not provider:
            print("Select messenger: 1) Bale  2) Telegram  3) WhatsApp")
            choice = input("Number: ").strip().translate(str.maketrans("۱۲۳", "123"))
            provider = {"1": "bale", "2": "telegram", "3": "whatsapp"}.get(choice)
            if not provider:
                raise ValueError("Enter 1, 2 or 3")
        asyncio.run(
            run(args.message_selector, provider, not args.skip_voice_processing, args.resume_report)
        )
    except KeyboardInterrupt:
        print("\nBackup stopped at your request.")
    except Exception as error:
        print(f"\nError: {error}")
        raise SystemExit(1)
