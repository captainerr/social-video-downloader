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


def _ydl_opts_base() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": YT_DLP_TIMEOUT,
    }
    if os.path.isfile(_COOKIES_FILE):
        opts["cookiefile"] = _COOKIES_FILE
    return opts


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

    ydl_opts = _ydl_opts_base()

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).split("\n")[0] if str(e) else "Failed to extract video."
        raise HTTPException(status_code=422, detail=msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e!s}")

    if not info:
        raise HTTPException(status_code=422, detail="No video found at this URL.")

    title = info.get("title") or "video"
    # Sanitize filename
    safe_title = re.sub(r'[^\w\s\-.]', '', title)[:80].strip() or "video"
    filename = f"{safe_title}.mp4"

    # Always download via yt-dlp and stream the file. CDNs like video.twimg.com
    # return 403 when the browser opens the direct URL (wrong Referer); streaming
    # through our server lets yt-dlp send the correct headers when fetching.
    out_tmpl = str(Path(tempfile.gettempdir()) / "svd_%(id)s.%(ext)s")
    ydl_opts = {**_ydl_opts_base(), "outtmpl": out_tmpl, "format": "best[ext=mp4]/best"}

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
        raise HTTPException(status_code=422, detail=msg)
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
