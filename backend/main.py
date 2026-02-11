"""
Social video downloader API: accepts a URL, uses yt-dlp to extract the video,
returns redirect to direct URL or streams the file.
"""
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import certifi
import yt_dlp

# Use certifi's CA bundle so SSL verification works on macOS and other environments
# where the default trust store is missing or incomplete.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

ALLOWED_ORIGINS = [
    "https://twitter.com",
    "https://x.com",
    "https://www.twitter.com",
    "https://www.x.com",
    "https://instagram.com",
    "https://www.instagram.com",
    "https://tiktok.com",
    "https://www.tiktok.com",
    "https://vm.tiktok.com",
    "https://youtube.com",
    "https://www.youtube.com",
    "https://youtu.be",
]

ALLOWED_NETLOCS = set()
for origin in ALLOWED_ORIGINS:
    parsed = urlparse(origin)
    ALLOWED_NETLOCS.add(parsed.netloc)
# Allow mobile variants
ALLOWED_NETLOCS.update({
    "mobile.twitter.com", "m.twitter.com", "m.instagram.com",
    "m.youtube.com", "music.youtube.com",
})

YT_DLP_TIMEOUT = 60
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW = 60  # seconds
_rate_limit: dict[str, list[float]] = {}

# Optional: path to Netscape cookies file for Instagram/YouTube when they require login or block bots.
# Set YT_DLP_COOKIES_FILE or place a file at backend/cookies.txt (export from browser).
_COOKIES_FILE = os.environ.get("YT_DLP_COOKIES_FILE") or str(Path(__file__).resolve().parent / "cookies.txt")


# Browser-like headers to reduce "bot" blocks (no cookies, no cost)
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-us,en;q=0.9",
}

def _ydl_opts_base(extractor_args: str | None = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": YT_DLP_TIMEOUT,
        "http_headers": _HTTP_HEADERS,
    }
    if extractor_args:
        opts["extractor_args"] = extractor_args
    if os.path.isfile(_COOKIES_FILE):
        opts["cookiefile"] = _COOKIES_FILE
    return opts


def _is_youtube(url: str) -> bool:
    parsed = urlparse(url)
    netloc = (parsed.netloc or "").lower().split(":")[0]
    return netloc in ("youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com")


def _is_bot_or_login_error(msg: str) -> bool:
    msg_lower = (msg or "").lower()
    return any(
        x in msg_lower
        for x in ("bot", "login", "sign in", "cookies", "rate-limit", "rate limit", "not available")
    )


def _friendly_message(raw: str, url: str) -> str:
    """Return a short, friendly error when platforms block us (no cookies, no cost)."""
    if _is_bot_or_login_error(raw) and (_is_youtube(url) or "instagram" in raw.lower()):
        return (
            "This platform is blocking automated requests right now. "
            "Twitter and TikTok usually work — try one of those, or try again later."
        )
    return raw or "Failed to get video."


def _get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    if ip not in _rate_limit:
        _rate_limit[ip] = []
    times = _rate_limit[ip]
    times.append(now)
    times[:] = [t for t in times if now - t < RATE_LIMIT_WINDOW]
    if len(times) > RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
        )


app = FastAPI(title="Social Video Downloader")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DownloadRequest(BaseModel):
    url: HttpUrl


def is_allowed_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        netloc = parsed.netloc.lower()
        # Strip optional port
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        return netloc in ALLOWED_NETLOCS or any(
            netloc.endswith("." + n) for n in ALLOWED_NETLOCS
        )
    except Exception:
        return False


@app.post("/api/download")
async def download(request: Request, body: DownloadRequest):
    _check_rate_limit(_get_client_ip(request))
    url = str(body.url)
    if not is_allowed_url(url):
        raise HTTPException(
            status_code=400,
            detail="URL must be from Twitter/X, Instagram, TikTok, or YouTube.",
        )

    # Try extraction; for YouTube, retry with alternate clients if we hit bot/login blocks
    youtube_clients = [None, "youtube:player_client=android", "youtube:player_client=mweb"]
    last_error: str | None = None
    info = None
    winning_opts = None

    for extractor_arg in youtube_clients if _is_youtube(url) else [None]:
        opts = _ydl_opts_base(extractor_arg)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                winning_opts = opts
                break
        except yt_dlp.utils.DownloadError as e:
            last_error = str(e).split("\n")[0] if str(e) else "Failed to extract video."
            if _is_youtube(url) and _is_bot_or_login_error(last_error) and extractor_arg != youtube_clients[-1]:
                continue  # try next client
            raise HTTPException(status_code=422, detail=_friendly_message(last_error, url))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Extraction failed: {e!s}")

    if not info:
        raise HTTPException(status_code=422, detail=_friendly_message(last_error or "No video found.", url))

    title = info.get("title") or "video"
    safe_title = re.sub(r'[^\w\s\-.]', '', title)[:80].strip() or "video"
    filename = f"{safe_title}.mp4"

    out_tmpl = str(Path(tempfile.gettempdir()) / "svd_%(id)s.%(ext)s")
    ydl_opts = {**(winning_opts or _ydl_opts_base()), "outtmpl": out_tmpl, "format": "best[ext=mp4]/best"}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(url, download=True)
        if not result:
            raise HTTPException(status_code=422, detail="Download failed.")
        path = ydl.prepare_filename(result)
        if not Path(path).exists():
            raise HTTPException(status_code=500, detail="File not found after download.")
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).split("\n")[0] if str(e) else "Download failed."
        raise HTTPException(status_code=422, detail=_friendly_message(msg, url))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download failed: {e!s}")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve frontend (run from backend dir with frontend as sibling or set path)
_frontend_path = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_path), html=True), name="frontend")
