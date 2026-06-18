"""Download media from a social-media URL (Instagram, TikTok, Facebook, X, YouTube…)
using yt-dlp, returning a local temp file ready for the Drive upload pipeline.

Uses a single-file mp4 format so no ffmpeg merge step is required (reels / TikToks
are already progressive mp4). Blocking — call from a thread executor.
"""
import glob
import json
import logging
import mimetypes
import os
import re
import shutil
import urllib.parse
import urllib.request

import yt_dlp

logger = logging.getLogger(__name__)

# Domains this is meant for (yt-dlp supports many more; this is just for messaging)
SUPPORTED_HINTS = (
    "instagram.com", "tiktok.com", "facebook.com", "fb.watch",
    "twitter.com", "x.com", "youtube.com", "youtu.be",
)


def _safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:80].strip() or "download")


def download_from_url(url: str, dest_dir: str) -> tuple[str, str, str, int]:
    """Download the first media item at `url` into `dest_dir`.

    Returns (file_path, file_name, mime_type, size_bytes).
    Raises on failure.
    """
    os.makedirs(dest_dir, exist_ok=True)
    outtmpl = os.path.join(dest_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        # prefer a single progressive mp4 → no ffmpeg merge needed
        "format": "best[ext=mp4]/mp4/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "retries": 2,
        "socket_timeout": 20,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        },
    }

    # Optional cookies for sites that require login (Instagram, Facebook…).
    # Set YTDLP_COOKIES in .env to a Netscape-format cookies.txt path.
    cookies = os.getenv("YTDLP_COOKIES", "").strip()
    if cookies and os.path.exists(cookies):
        ydl_opts["cookiefile"] = cookies
        logger.info("Using cookies file: %s", cookies)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        # carousels / playlists → take the first real entry
        if info.get("entries"):
            entries = [e for e in info["entries"] if e]
            if not entries:
                raise RuntimeError("No downloadable media found at that link.")
            info = entries[0]

        path = ydl.prepare_filename(info)
        if not os.path.exists(path):
            base = os.path.splitext(path)[0]
            candidates = glob.glob(base + ".*")
            if not candidates:
                raise RuntimeError("Download produced no file.")
            path = candidates[0]

    title = info.get("title") or info.get("description") or info.get("id") or "video"
    ext = os.path.splitext(path)[1] or ".mp4"
    file_name = _safe_name(title) + ext
    mime_type = mimetypes.guess_type(path)[0] or (
        "video/mp4" if ext.lower() in (".mp4", ".mov", ".webm", ".m4v") else "application/octet-stream"
    )
    size = os.path.getsize(path)
    logger.info("Downloaded %s (%d bytes) from %s", file_name, size, url)
    return path, file_name, mime_type, size


# ── Third-party API fallback (Cobalt-compatible) ─────────────────────────────

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _http_json(api_url: str, payload: dict, api_key: str, timeout: int = 30) -> dict:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": _UA,
    }
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"
    req = urllib.request.Request(
        api_url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _download_url_to(media_url: str, path: str, timeout: int = 120) -> None:
    req = urllib.request.Request(media_url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(path, "wb") as f:
        shutil.copyfileobj(resp, f)


def _http_get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def tikwm_download(url: str, dest_dir: str) -> tuple[str, str, str, int]:
    """Dedicated TikTok downloader via the free tikwm.com API (no key needed)."""
    api = "https://www.tikwm.com/api/?hd=1&url=" + urllib.parse.quote(url, safe="")
    data = _http_get_json(api)
    if data.get("code") != 0:
        raise RuntimeError(data.get("msg") or "tikwm error")
    d = data.get("data") or {}
    media = d.get("hdplay") or d.get("play") or d.get("wmplay")
    if not media:
        raise RuntimeError("tikwm returned no video URL.")
    if media.startswith("/"):
        media = "https://www.tikwm.com" + media

    title = d.get("title") or str(d.get("id") or "tiktok")
    os.makedirs(dest_dir, exist_ok=True)
    file_name = _safe_name(title) + ".mp4"
    path = os.path.join(dest_dir, file_name)
    _download_url_to(media, path)
    size = os.path.getsize(path)
    logger.info("Downloaded %s (%d bytes) via tikwm from %s", file_name, size, url)
    return path, file_name, "video/mp4", size


def cobalt_download(url: str, dest_dir: str) -> tuple[str, str, str, int]:
    """Fallback downloader using a Cobalt-compatible API.

    Configure with env vars:
      COBALT_API_URL  — e.g. https://your-instance/  (required for fallback)
      COBALT_API_KEY  — optional Api-Key for instances that require it
    """
    api_url = os.getenv("COBALT_API_URL", "").strip()
    if not api_url:
        raise RuntimeError("No fallback API configured (set COBALT_API_URL).")
    api_url = api_url.rstrip("/") + "/"
    api_key = os.getenv("COBALT_API_KEY", "").strip()

    data = _http_json(api_url, {
        "url": url,
        "videoQuality": "1080",
        "filenameStyle": "basic",
    }, api_key)

    status = data.get("status")
    filename = data.get("filename")
    if status in ("redirect", "tunnel", "stream"):
        media_url = data.get("url")
    elif status == "picker":
        items = data.get("picker") or []
        vids = [i for i in items if i.get("type") == "video"] or items
        if not vids:
            raise RuntimeError("API returned no downloadable media.")
        media_url = vids[0].get("url")
        filename = filename or vids[0].get("filename")
    else:
        err = data.get("error", {})
        raise RuntimeError(err.get("code") if isinstance(err, dict) else f"API status: {status}")

    if not media_url:
        raise RuntimeError("API returned no media URL.")

    os.makedirs(dest_dir, exist_ok=True)
    filename = filename or "download.mp4"
    base, ext = os.path.splitext(filename)
    file_name = _safe_name(base) + (ext or ".mp4")
    path = os.path.join(dest_dir, file_name)

    _download_url_to(media_url, path)
    size = os.path.getsize(path)
    mime_type = mimetypes.guess_type(path)[0] or "video/mp4"
    logger.info("Downloaded %s (%d bytes) via API from %s", file_name, size, url)
    return path, file_name, mime_type, size


def fetch_media(url: str, dest_dir: str) -> tuple[str, str, str, int]:
    """Try yt-dlp first; on failure fall back to dedicated/configured APIs."""
    try:
        return download_from_url(url, dest_dir)
    except Exception as primary_exc:
        logger.info("yt-dlp failed (%s) — trying fallbacks", primary_exc)
        errors = [f"yt-dlp: {primary_exc}"]

        # TikTok: dedicated free API (no key, very reliable)
        if "tiktok.com" in url.lower():
            try:
                return tikwm_download(url, dest_dir)
            except Exception as tt_exc:
                logger.info("tikwm failed (%s)", tt_exc)
                errors.append(f"tikwm: {tt_exc}")

        # Generic Cobalt-compatible API (if configured)
        try:
            return cobalt_download(url, dest_dir)
        except Exception as cobalt_exc:
            errors.append(f"api: {cobalt_exc}")

        raise RuntimeError(" | ".join(errors))
