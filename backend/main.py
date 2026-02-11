"""
Social video downloader API: accepts a URL, uses yt-dlp to extract the video,
returns redirect to direct URL or streams the file.
"""
import asyncio
import logging
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
from fastapi import BackgroundTasks, FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

logger = logging.getLogger("svd")
logging.basicConfig(level=logging.INFO)

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

def _ydl_opts_base(*, is_youtube: bool = False, use_pot: bool = False) -> dict:
    """Build yt-dlp options.

    For YouTube we intentionally pass *no* custom headers and *no* player_client
    overrides so the behaviour matches ``yt-dlp <url>`` on the CLI (which works).
    The POT provider is only injected on an explicit retry so the first attempt is
    as vanilla as possible.
    """
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": YT_DLP_TIMEOUT,
    }
    # Only pass custom headers for non-YouTube; yt-dlp's defaults work better
    # for YouTube (our browser-like UA from a Linux server triggers bot detection).
    if not is_youtube:
        opts["http_headers"] = _HTTP_HEADERS
    # Optional PO-token provider (YouTube only, second attempt)
    if use_pot:
        pot_url = os.environ.get("YT_DLP_POT_PROVIDER_URL", "").strip()
        if pot_url:
            opts["extractor_args"] = {
                "youtubepot-bgutilhttp": {"base_url": pot_url},
            }
    if os.path.isfile(_COOKIES_FILE):
        opts["cookiefile"] = _COOKIES_FILE
    return opts


def _is_youtube(url: str) -> bool:
    parsed = urlparse(url)
    netloc = (parsed.netloc or "").lower().split(":")[0]
    return netloc in ("youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com")


def _looks_like_bot_block(msg: str) -> bool:
    """Return True if the error looks like a bot / login / rate-limit block."""
    msg_lower = (msg or "").lower()
    return any(
        kw in msg_lower
        for kw in ("bot", "login", "sign in", "cookies", "rate-limit", "rate limit", "not available")
    )


def _get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    if ip not in _rate_limit:
        _rate_limit[ip] = []
    times = _rate_limit[ip]
    # Prune expired entries first, then check before appending
    times[:] = [t for t in times if now - t < RATE_LIMIT_WINDOW]
    if len(times) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
        )
    times.append(now)


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
async def download(request: Request, body: DownloadRequest, bg: BackgroundTasks):
    _check_rate_limit(_get_client_ip(request))
    url = str(body.url)
    if not is_allowed_url(url):
        raise HTTPException(
            status_code=400,
            detail="URL must be from Twitter/X, Instagram, TikTok, or YouTube.",
        )

    is_yt = _is_youtube(url)
    has_pot = bool(os.environ.get("YT_DLP_POT_PROVIDER_URL", "").strip())

    # Build a list of option sets to try.
    # Attempt 1 is always vanilla (mirrors `yt-dlp <url>` on the CLI).
    # For YouTube, if a POT provider is configured we add a second attempt that uses it.
    attempts: list[dict] = [_ydl_opts_base(is_youtube=is_yt)]
    if is_yt and has_pot:
        attempts.append(_ydl_opts_base(is_youtube=True, use_pot=True))

    # --- Phase 1: extract metadata -------------------------------------------
    info = None
    winning_opts = None
    last_error: str = ""

    for i, opts in enumerate(attempts):
        try:
            logger.info("Attempt %d/%d for %s (opts keys: %s)", i + 1, len(attempts), url, list(opts.keys()))
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                winning_opts = opts
                break
        except yt_dlp.utils.DownloadError as e:
            last_error = str(e).split("\n")[0] if str(e) else "Failed to extract video."
            logger.warning("Extraction attempt %d failed for %s: %s", i + 1, url, last_error)
            # If this was a bot/rate-limit block and we have more attempts, retry
            if _looks_like_bot_block(last_error) and i < len(attempts) - 1:
                await asyncio.sleep(2)
                continue
            raise HTTPException(status_code=422, detail=last_error)
        except Exception as e:
            logger.exception("Unexpected error extracting %s", url)
            raise HTTPException(status_code=500, detail=f"Extraction failed: {e!s}")

    if not info or not isinstance(info, dict):
        raise HTTPException(status_code=422, detail=last_error or "No video found.")

    # --- Phase 2: download the file -------------------------------------------
    title = info.get("title") or "video"
    safe_title = re.sub(r'[^\w\s\-.]', '', title)[:80].strip() or "video"
    filename = f"{safe_title}.mp4"

    out_tmpl = str(Path(tempfile.gettempdir()) / "svd_%(id)s.%(ext)s")
    dl_opts = {
        **(winning_opts or _ydl_opts_base(is_youtube=is_yt)),
        "outtmpl": out_tmpl,
        "format": "best[ext=mp4]/best",
    }

    try:
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            result = ydl.extract_info(url, download=True)
            if not result or not isinstance(result, dict):
                raise HTTPException(status_code=422, detail="Download failed.")
            path = ydl.prepare_filename(result)
        if not Path(path).exists():
            raise HTTPException(status_code=500, detail="File not found after download.")
        # Schedule temp file deletion after the response is sent
        bg.add_task(os.unlink, path)
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).split("\n")[0] if str(e) else "Download failed."
        logger.warning("Download failed for %s: %s", url, msg)
        raise HTTPException(status_code=422, detail=msg)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error downloading %s", url)
        raise HTTPException(status_code=500, detail=f"Download failed: {e!s}")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve frontend (run from backend dir with frontend as sibling or set path)
_frontend_path = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_path), html=True), name="frontend")
