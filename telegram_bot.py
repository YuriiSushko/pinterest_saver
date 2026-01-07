import os
import asyncio
import logging
import shutil
from pathlib import Path
import mimetypes

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

from pinterest import extract_urls, is_pinterest_url, resolve_url, normalize_pin_url, find_pinimg_mp4, extract_best_media
from downloader import download_to_temp, ytdlp_try_download

logger = logging.getLogger("pinterest_bot")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return

    urls = extract_urls(msg.text)
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
                        await msg.reply_video(video=f, read_timeout=120, write_timeout=120, connect_timeout=20)
                elif ext == ".gif" or mime == "image/gif":
                    with open(local_path, "rb") as f:
                        await msg.reply_animation(animation=f, read_timeout=120, write_timeout=120, connect_timeout=20)
                else:
                    with open(local_path, "rb") as f:
                        await msg.reply_document(document=f, read_timeout=120, write_timeout=120, connect_timeout=20)
                continue

            photo_url, video_url = await asyncio.to_thread(extract_best_media, resolved)
            if video_url:
                tmp_path, ext, ct = await asyncio.to_thread(download_to_temp, video_url)
                with open(tmp_path, "rb") as f:
                    await msg.reply_video(video=f, read_timeout=120, write_timeout=120, connect_timeout=20)
            elif photo_url:
                tmp_path, ext, ct = await asyncio.to_thread(download_to_temp, photo_url)
                if ext == ".gif":
                    with open(tmp_path, "rb") as f:
                        await msg.reply_animation(animation=f, read_timeout=120, write_timeout=120, connect_timeout=20)
                else:
                    with open(tmp_path, "rb") as f:
                        await msg.reply_photo(photo=f, read_timeout=120, write_timeout=120, connect_timeout=20)
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

def build_app(token: str) -> Application:
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
    return app
