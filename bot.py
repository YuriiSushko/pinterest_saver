import os
import re
import asyncio
import html
import logging
import shutil
import tempfile
import sys
from urllib.parse import urlparse, quote, unquote
from pathlib import Path
import mimetypes
import subprocess

from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("pinterest_bot")

URL_RE = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)
PIN_ID_RE = re.compile(r"/pin/(\d+)")
MP4_RE = re.compile(r"https://v\.pinimg\.com/[^\s\"'<>]+\.mp4[^\s\"'<>]*", re.IGNORECASE)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

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

def extract_best_media(resolved_pin_url: str) -> tuple[str | None, str | None]:
    data = pinterest_oembed(resolved_pin_url)
    thumb = None
    if data:
        thumb = data.get("thumbnail_url")
    og = scrape_og(resolved_pin_url)
    v = og.get("og:video") or og.get("og:video:url")
    img = og.get("og:image") or thumb
    return img, v

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

def download_to_temp(url: str, timeout: int = 60, max_bytes: int = 100 * 1024 * 1024) -> tuple[str, str, str | None]:
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

def find_pinimg_mp4(pin_url: str, timeout: int = 20) -> str | None:
    r = SESSION.get(pin_url, timeout=timeout)
    if r.status_code != 200:
        return None
    text = r.text
    m = MP4_RE.search(text)
    if m:
        return m.group(0)
    candidates = MP4_RE.findall(text)
    if candidates:
        return candidates[0]
    return None

def ytdlp_try_download(url: str, timeout: int = 120) -> str | None:
    tmpdir = tempfile.mkdtemp(prefix="pinsaver_")
    outtmpl = str(Path(tmpdir) / "media.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--no-warnings",
        "-f", "bv*+ba/best/best",
        "-o", outtmpl,
        url,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "")
        if "No video formats found" in err:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return None
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(err[:2000])
    files = sorted(Path(tmpdir).glob("media.*"), key=lambda x: x.stat().st_size, reverse=True)
    if not files:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None
    return str(files[0])

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return

    urls = [m.group(1) for m in URL_RE.finditer(msg.text)]
    pin_urls = [u for u in urls if is_pinterest_url(u)]
    if not pin_urls:
        return

    for raw_url in pin_urls:
        tmp_path = None
        tmp_dir = None
        try:
            resolved = await asyncio.to_thread(resolve_url, raw_url)
            resolved = normalize_pin_url(resolved)

            mp4_url = await asyncio.to_thread(find_pinimg_mp4, resolved)
            if mp4_url:
                tmp_path, ext, ct = await asyncio.to_thread(download_to_temp, mp4_url)
                with open(tmp_path, "rb") as f:
                    await msg.reply_video(video=f, caption="Saved from Pinterest", read_timeout=120, write_timeout=120, connect_timeout=20)
                continue

            local_path = await asyncio.to_thread(ytdlp_try_download, resolved)
            if local_path:
                tmp_dir = str(Path(local_path).parent)
                ext = Path(local_path).suffix.lower()
                mime, _ = mimetypes.guess_type(local_path)

                if ext in [".mp4", ".webm", ".mov", ".mkv"]:
                    with open(local_path, "rb") as f:
                        await msg.reply_video(video=f, caption="Saved from Pinterest", read_timeout=120, write_timeout=120, connect_timeout=20)
                elif ext == ".gif" or mime == "image/gif":
                    with open(local_path, "rb") as f:
                        await msg.reply_animation(animation=f, caption="Saved from Pinterest", read_timeout=120, write_timeout=120, connect_timeout=20)
                else:
                    with open(local_path, "rb") as f:
                        await msg.reply_document(document=f, caption="Saved from Pinterest", read_timeout=120, write_timeout=120, connect_timeout=20)
                continue

            photo_url, video_url = await asyncio.to_thread(extract_best_media, resolved)
            if video_url:
                tmp_path, ext, ct = await asyncio.to_thread(download_to_temp, video_url)
                with open(tmp_path, "rb") as f:
                    await msg.reply_video(video=f, caption="Saved from Pinterest", read_timeout=120, write_timeout=120, connect_timeout=20)
            elif photo_url:
                tmp_path, ext, ct = await asyncio.to_thread(download_to_temp, photo_url)
                if ext == ".gif":
                    with open(tmp_path, "rb") as f:
                        await msg.reply_animation(animation=f, caption="Saved from Pinterest", read_timeout=120, write_timeout=120, connect_timeout=20)
                else:
                    with open(tmp_path, "rb") as f:
                        await msg.reply_photo(photo=f, caption="Saved from Pinterest", read_timeout=120, write_timeout=120, connect_timeout=20)
            else:
                await msg.reply_text("Could not extract media from that pin.")
        except Exception as e:
            logger.exception("failed")
            await msg.reply_text(f"Error: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            if tmp_dir and os.path.exists(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
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
