# Social Video Downloader

A minimal webapp to download videos from YouTube, Twitter/X, Instagram, and TikTok by pasting a link.

## Setup

1. Create a virtual environment and install dependencies:

```bash
cd social-video-downloader/backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

2. Run the server (serves both API and frontend):

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

3. Open [http://localhost:8000](http://localhost:8000), paste a video URL, and click Download.

## Project layout

- `backend/main.py` – FastAPI app: URL validation, yt-dlp extraction, download/stream response, optional rate limiting
- `backend/requirements.txt` – Python dependencies
- `frontend/index.html` – Single-page UI (input, button, loading state, errors, disclaimer)

## Behaviour

- **Allowed URLs**: YouTube, Twitter/X, Instagram, TikTok (origin allowlist in `main.py`).
- **Rate limit**: 10 requests per minute per IP (configurable in `main.py`).
- **Response**: The backend uses yt-dlp to download the video (with correct headers for CDNs), then streams the file to the client with `Content-Disposition: attachment`.

## Hosting

Use a **long-running server** (VPS or PaaS), not serverless (Lambda, Cloud Functions). yt-dlp can run 30–60+ seconds per request and may write temp files; serverless time limits and binary restrictions usually make it a poor fit.

**What you need**

- **OS**: Linux (or any OS where Python 3.11+ and yt-dlp run).
- **Resources**: A small instance is enough for light traffic (e.g. 1 vCPU, 1 GB RAM). Scale up if you have many concurrent downloads.
- **Bandwidth**: Each download streams a full video; expect significant egress if traffic grows.
- **Disk**: Temp files go to the system temp dir; ensure a few hundred MB free (or use a ramdisk for high load).
- **HTTPS**: Put the app behind a reverse proxy (Caddy, nginx, Traefik) with TLS, or use a PaaS that provides it.

**Step-by-step**: See **[Deploy on Vultr](docs/DEPLOY-VULTR.md)** for a full walkthrough (instance, systemd, Caddy, HTTPS).

**Options**

| Type | Examples | Notes |
|------|----------|--------|
| **VPS** | DigitalOcean Droplet, Linode, Vultr, Hetzner | Full control; install Python, run uvicorn (or gunicorn + uvicorn workers), use systemd or Docker. |
| **Cloud VM** | AWS EC2, GCP e2, Azure B1s | Same idea as VPS; add a firewall and reverse proxy. |
| **PaaS** | Railway, Render, Fly.io | Deploy via Git + Dockerfile or buildpack; check that yt-dlp (and ffmpeg if you add it later) are supported. |
| **Docker** | Any host with Docker | Use a Dockerfile that installs Python, dependencies, and yt-dlp; run uvicorn. Easiest for consistent deploys. |

**Quick Docker example** (optional): From the project root, `docker build -t svd .` and run a container that exposes port 8000; use a Dockerfile that copies `backend/` and `frontend/`, installs from `backend/requirements.txt`, and runs `uvicorn main:app --host 0.0.0.0 --port 8000`.

## Optional: Cookies for Instagram / YouTube

If you see errors like "login required", "rate-limit reached", or "Sign in to confirm you're not a bot", Instagram or YouTube may be blocking the server. You can supply browser cookies so yt-dlp can authenticate:

1. **Export cookies** from your browser (while logged in to Instagram/YouTube):
   - Use a Netscape-format export, e.g. the [Get cookies.txt](https://github.com/rotemdan/ExportCookies) extension (Chrome/Firefox) or [cookies.txt](https://add0n.com/cookies.html). Export for the site(s) you need.
2. **Put the file on the server** as `backend/cookies.txt`, or set the path with an env var:
   ```bash
   export YT_DLP_COOKIES_FILE=/path/to/cookies.txt
   ```
3. **Restart the app** so it picks up the file: `systemctl restart svd` (or restart uvicorn).

The app only uses the cookies file if it exists; it is optional. Do not commit `cookies.txt` to Git (it is in `.gitignore`). Cookies expire; re-export periodically if downloads start failing again.

See [yt-dlp FAQ on cookies](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) for more.

## Disclaimer

For personal use only. Downloading may violate platform terms of service or copyright. Use at your own risk.
