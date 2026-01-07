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
