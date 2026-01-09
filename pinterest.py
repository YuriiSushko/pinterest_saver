import re
import html
from urllib.parse import urlparse, quote

import requests
from bs4 import BeautifulSoup

URL_RE = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)
PIN_ID_RE = re.compile(r"/pin/(\d+)")
MP4_RE = re.compile(r"https://v\.pinimg\.com/[^\s\"'<>]+\.mp4[^\s\"'<>]*", re.IGNORECASE)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
GIF_URL_RE = re.compile(r"https://i\.pinimg\.com/[^\s\"'<>]+\.gif[^\s\"'<>]*", re.IGNORECASE)
GIF_HINT_RE = re.compile(r"(animated[_-]?gif|\"is_gif\"\s*:\s*true|\"isGif\"\s*:\s*true|\"content_type\"\s*:\s*\"animated_gif\"|\"pin_type\"\s*:\s*\"gif\")", re.IGNORECASE)
VIDEO_HINT_RE = re.compile(r"(\"type\"\s*:\s*\"video\"|\"is_video\"\s*:\s*true|\"isVideo\"\s*:\s*true|\"content_type\"\s*:\s*\"video\")", re.IGNORECASE)

def fetch_pin_html(pin_url: str, timeout: int = 20) -> str:
    r = SESSION.get(pin_url, timeout=timeout)
    if r.status_code != 200:
        return ""
    return r.text or ""

def find_pinimg_gif_from_html(html_text: str) -> str | None:
    m = GIF_URL_RE.search(html_text)
    if m:
        return m.group(0)
    return None

def find_pinimg_mp4_from_html(html_text: str) -> str | None:
    m = MP4_RE.search(html_text)
    if m:
        return m.group(0)
    candidates = MP4_RE.findall(html_text)
    if candidates:
        return candidates[0]
    return None

def classify_pin_from_html(html_text: str) -> str:
    if not html_text:
        return "unknown"
    if GIF_HINT_RE.search(html_text):
        return "gif"
    if VIDEO_HINT_RE.search(html_text):
        return "video"
    return "unknown"

def extract_urls(text: str) -> list[str]:
    return [m.group(1) for m in URL_RE.finditer(text or "")]

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

def find_pinimg_gif(pin_url: str, timeout: int = 20) -> str | None:
    r = SESSION.get(pin_url, timeout=timeout)
    if r.status_code != 200:
        return None
    text = r.text
    m = GIF_RE.search(text)
    if m:
        return m.group(0)
    return None

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
