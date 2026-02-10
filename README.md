# Social Video Downloader

A minimal webapp to download videos from Twitter/X, Instagram, and TikTok by pasting a link.

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

- **Allowed URLs**: Twitter/X, Instagram, TikTok only (origin allowlist).
- **Rate limit**: 10 requests per minute per IP (configurable in `main.py`).
- **Response**: When possible the backend returns a direct video URL and the frontend opens it to trigger the download; otherwise the backend downloads the video and streams it with `Content-Disposition: attachment`.

## Disclaimer

For personal use only. Downloading may violate platform terms of service or copyright. Use at your own risk.
