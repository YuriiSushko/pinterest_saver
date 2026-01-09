import os
import shutil
import tempfile
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote
import subprocess

import requests

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

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

def mp4_to_gif(mp4_path: str, max_seconds: int = 12, width: int = 480) -> str:
    tmpdir = tempfile.mkdtemp(prefix="pinsaver_gif_")
    gif_path = str(Path(tmpdir) / "out.gif")
    palette = str(Path(tmpdir) / "palette.png")

    cmd_palette = [
        "ffmpeg", "-y",
        "-t", str(max_seconds),
        "-i", mp4_path,
        "-vf", f"fps=15,scale={width}:-1:flags=lanczos,palettegen",
        palette
    ]
    cmd_gif = [
        "ffmpeg", "-y",
        "-t", str(max_seconds),
        "-i", mp4_path,
        "-i", palette,
        "-lavfi", f"fps=15,scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse",
        gif_path
    ]

    p1 = subprocess.run(cmd_palette, capture_output=True, text=True)
    if p1.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError((p1.stderr or p1.stdout or "ffmpeg palettegen failed")[:2000])

    p2 = subprocess.run(cmd_gif, capture_output=True, text=True)
    if p2.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError((p2.stderr or p2.stdout or "ffmpeg gif failed")[:2000])

    return gif_path

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
