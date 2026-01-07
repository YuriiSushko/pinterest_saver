import os
import re
import asyncio
import html
import logging
import shutil
import tempfile
from urllib.parse import urlparse, quote, unquote

from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

import subprocess
from pathlib import Path
import mimetypes

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("pinterest_bot")

URL_RE = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
PIN_ID_RE = re.compile(r"/pin/(\d+)")

def is_pinterest_url(u: str) -> bool:
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        return False
    return host == "pin.it" or "pinterest." in host

def resolve_url(url: str, timeout: int = 20) -> str:
    r = SESSION.get(url, allow_redirects=True, timeout=timeout)
    return r.url

def normalize_pin_url(url: str) -> str:
    m = PIN_ID_RE.search(url)
    if m:
        return f"https://www.pinterest.com/pin/{m.group(1)}/"
    return url

def ytdlp_download(url: str, timeout: int = 120) -> str:
    tmpdir = tempfile.mkdtemp(prefix="pinsaver_")
    outtmpl = str(Path(tmpdir) / "media.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "-f", "bv*+ba/best",
        "-o", outtmpl,
        url,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "yt-dlp failed")[:2000])
    files = sorted(Path(tmpdir).glob("media.*"), key=lambda x: x.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("yt-dlp produced no files")
    return str(files[0])

def pinterest_oembed(pin_url: str, timeout: int = 20) -> dict | None:
    endpoint = "https://www.pinterest.com/oembed.json?url=" + quote(pin_url, safe="")
    r = SESSION.get(endpoint, timeout=timeout)
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if "error" in data:
        return None
    return data

def scrape_og(pin_url: str, timeout: int = 20) -> dict:
    r = SESSION.get(pin_url, timeout=timeout)
    if r.status_code != 200:
        return {}
    soup = BeautifulSoup(r.text, "html.parser")
    out = {}
    for prop in ["og:image", "og:video", "og:video:url", "og:type"]:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            out[prop] = html.unescape(tag["content"])
    return out

def pick_media(resolved_pin_url: str) -> tuple[str | None, str]:
    data = pinterest_oembed(resolved_pin_url)
    if data:
        thumb = data.get("thumbnail_url")
        if thumb:
            return thumb, "photo"
    og = scrape_og(resolved_pin_url)
    v = og.get("og:video") or og.get("og:video:url")
    img = og.get("og:image")
    og_type = (og.get("og:type") or "").lower()
    if v:
        kind = "video"
        if "gif" in og_type:
            kind = "gif"
        return v, kind
    if img:
        kind = "photo"
        if "gif" in og_type or img.lower().endswith(".gif"):
            kind = "gif"
        return img, kind
    return None, "none"

def sniff_extension(url: str, content_type: str | None) -> str:
    path = unquote(urlparse(url).path or "").lower()
    for ext in [".mp4", ".mov", ".webm", ".gif", ".jpg", ".jpeg", ".png"]:
        if path.endswith(ext):
            return ext
    if content_type:
        ct = content_type.lower()
        if "video/mp4" in ct:
            return ".mp4"
        if "video/webm" in ct:
            return ".webm"
        if "image/gif" in ct:
            return ".gif"
        if "image/png" in ct:
            return ".png"
        if "image/jpeg" in ct:
            return ".jpg"
    return ".bin"

def download_to_temp(url: str, timeout: int = 40, max_bytes: int = 100 * 1024 * 1024) -> tuple[str, str, str | None]:
    with SESSION.get(url, stream=True, timeout=timeout, allow_redirects=True) as r:
        r.raise_for_status()
        content_type = r.headers.get("Content-Type")
        ext = sniff_extension(url, content_type)
        fd, path = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        total = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("File too large")
                f.write(chunk)
    return path, ext, content_type

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text
    urls = [m.group(1) for m in URL_RE.finditer(text)]
    pin_urls = [u for u in urls if is_pinterest_url(u)]
    if not pin_urls:
        return

    for raw in pin_urls:
        tmp_path = None
        try:
            resolved = await asyncio.to_thread(resolve_url, raw)
            local_path = await asyncio.to_thread(ytdlp_download, resolved)

            ext = Path(local_path).suffix.lower()
            mime, _ = mimetypes.guess_type(local_path)

            if ext in [".mp4", ".webm", ".mov", ".mkv"]:
                with open(local_path, "rb") as f:
                    await msg.reply_video(video=f, read_timeout=120, write_timeout=120, connect_timeout=20)
            elif ext == ".gif" or (mime == "image/gif"):
                with open(local_path, "rb") as f:
                    await msg.reply_animation(animation=f, read_timeout=120, write_timeout=120, connect_timeout=20)
            else:
                with open(local_path, "rb") as f:
                    await msg.reply_document(document=f, read_timeout=120, write_timeout=120, connect_timeout=20)


        except Exception as e:
            logger.exception("failed")
            await msg.reply_text(f"Error: {e}")
        finally:
            try:
                if local_path and os.path.exists(local_path):
                    shutil.rmtree(str(Path(local_path).parent), ignore_errors=True)
            except Exception:
                pass

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("error update=%s", update, exc_info=context.error)

def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is missing")

    request = HTTPXRequest(
        connection_pool_size=20,
        read_timeout=60,
        write_timeout=60,
        connect_timeout=20,
        pool_timeout=20
    )

    app = Application.builder().token(token).request(request).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()
