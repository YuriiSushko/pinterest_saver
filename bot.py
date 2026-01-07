import os
import re
import asyncio
import html
import logging
from urllib.parse import urlparse, quote
from dotenv import load_dotenv

import requests
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("pinterest_bot")

URL_RE = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

def is_pinterest_url(u: str) -> bool:
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        return False
    return host == "pin.it" or "pinterest." in host

def resolve_url(url: str, timeout: int = 15) -> str:
    logger.debug("resolve_url start url=%s timeout=%s", url, timeout)
    r = requests.get(
        url,
        allow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    logger.debug(
        "resolve_url done status=%s final_url=%s history_len=%s",
        r.status_code,
        r.url,
        len(r.history),
    )
    return r.url

def pinterest_oembed(pin_url: str, timeout: int = 15) -> dict | None:
    endpoint = "https://www.pinterest.com/oembed.json?url=" + quote(pin_url, safe="")
    logger.debug("oembed start pin_url=%s endpoint=%s", pin_url, endpoint)
    r = requests.get(endpoint, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    logger.debug("oembed http status=%s", r.status_code)
    if r.status_code != 200:
        try:
            logger.debug("oembed non-200 body=%s", r.text[:2000])
        except Exception:
            pass
        return None
    try:
        data = r.json()
    except Exception:
        logger.exception("oembed json parse failed")
        return None
    if "error" in data:
        logger.debug("oembed returned error=%s", data.get("error"))
        return None
    logger.debug(
        "oembed ok keys=%s provider=%s type=%s",
        list(data.keys()),
        data.get("provider_name"),
        data.get("type"),
    )
    return data

def scrape_og_media(pin_url: str, timeout: int = 15) -> tuple[str | None, str | None]:
    logger.debug("scrape_og start url=%s", pin_url)
    r = requests.get(pin_url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    logger.debug("scrape_og http status=%s len=%s", r.status_code, len(r.text or ""))
    if r.status_code != 200:
        return None, None
    soup = BeautifulSoup(r.text, "html.parser")

    def meta(prop: str) -> str | None:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return html.unescape(tag["content"])
        return None

    img = meta("og:image")
    vid = meta("og:video") or meta("og:video:url")
    logger.debug("scrape_og found og:image=%s og:video=%s", img, vid)
    return img, vid

def extract_best_media(pin_url: str) -> tuple[str | None, str | None]:
    logger.debug("extract_best_media start url=%s", pin_url)
    data = pinterest_oembed(pin_url)
    if data:
        thumb = data.get("thumbnail_url")
        embed_html = data.get("html", "")
        best_img = None

        logger.debug(
            "extract_best_media oembed thumb=%s embed_html_len=%s",
            thumb,
            len(embed_html or ""),
        )

        if embed_html:
            soup = BeautifulSoup(embed_html, "html.parser")
            img = soup.find("img")
            if img and img.get("src"):
                best_img = img["src"]

        if not best_img:
            best_img = thumb

        og_img, og_vid = scrape_og_media(pin_url)
        photo = og_img or best_img
        video = og_vid
        logger.debug("extract_best_media result photo=%s video=%s", photo, video)
        return photo, video

    photo, video = scrape_og_media(pin_url)
    logger.debug("extract_best_media fallback result photo=%s video=%s", photo, video)
    return photo, video

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        logger.debug("on_text ignored no message/text update=%s", update)
        return

    chat = msg.chat
    user = msg.from_user

    logger.info(
        "message received chat_id=%s chat_type=%s chat_title=%s user_id=%s username=%s text=%r",
        getattr(chat, "id", None),
        getattr(chat, "type", None),
        getattr(chat, "title", None),
        getattr(user, "id", None),
        getattr(user, "username", None),
        msg.text,
    )

    urls = [m.group(1) for m in URL_RE.finditer(msg.text)]
    pin_urls = [u for u in urls if is_pinterest_url(u)]

    logger.debug("extracted urls=%s pin_urls=%s", urls, pin_urls)

    if not pin_urls:
        return

    for raw_url in pin_urls:
        try:
            logger.info("processing url=%s", raw_url)
            resolved = await asyncio.to_thread(resolve_url, raw_url)
            logger.info("resolved url=%s -> %s", raw_url, resolved)

            photo_url, video_url = await asyncio.to_thread(extract_best_media, resolved)
            logger.info("media extracted photo=%s video=%s", photo_url, video_url)

            if video_url:
                logger.info("sending video")
                await msg.reply_video(video=video_url)
                logger.info("video sent")
            elif photo_url:
                logger.info("sending photo")
                await msg.reply_photo(photo=photo_url)
                logger.info("photo sent")
            else:
                logger.warning("no media extracted")
                await msg.reply_text("Idi nahui")
        except Exception:
            logger.exception("failed processing url=%s", raw_url)
            try:
                await msg.reply_text("Error while saving that pin.")
            except Exception:
                logger.exception("failed sending error message")

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("dispatcher error update=%s", update, exc_info=context.error)

def main() -> None:
    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is missing")

    logger.info("starting bot polling")
    app = Application.builder().token(bot_token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()
