"""
Urdu to Speech AI - single-file backend (main.py)

What this file does:
- Serves a small HTML+JS web UI at "/" (suitable for embedding in an Android WebView).
- Exposes POST /api/synthesize which proxies text -> Orator TTS on your server and returns audio binary.
- Handles common Orator response shapes:
  - binary audio (audio/mpeg, audio/wav, etc.)
  - JSON with base64 audio field ("audio", "audio_base64", "result")
  - JSON with an "url" pointing to an audio file (it will fetch that and return it)
- Does NOT contain any API keys. Set ORATOR_API_KEY and ORATOR_TTS_ENDPOINT in environment or a .env file.
- Minimal dependencies: fastapi, uvicorn, httpx, python-dotenv

Usage:
1) pip install -r requirements.txt
   requirements.txt should include:
     fastapi
     uvicorn[standard]
     httpx
     python-dotenv
2) Create a .env file or set environment variables:
     ORATOR_API_KEY=sk_...
     ORATOR_TTS_ENDPOINT=https://api.orator.example/v1/tts
   (replace ORATOR_TTS_ENDPOINT with your real Orator endpoint)
3) Run:
     uvicorn main:app --host 0.0.0.0 --port 7860
4) Point your Android WebView to http(s)://<server>:7860/

Security:
- Never commit ORATOR_API_KEY to source control.
- For production use HTTPS and protect the endpoint (auth, rate-limit, quotas).
"""

import os
import asyncio
import base64
import io
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx

load_dotenv()

ORATOR_API_KEY = os.getenv("sk_api_61ca8d1b81af22adbff32d1d73628d5d5d08e8fe4ed29aa7dea0a29c728cacea")
ORATOR_TTS_ENDPOINT = os.getenv("ORATOR_TTS_ENDPOINT", "https://api.orator.example/v1/tts")

app = FastAPI(title="Urdu to Speech AI (Lightweight)")

# Allow all origins for quick testing (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Urdu to Speech AI</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    textarea { width: 100%; height: 120px; font-size: 16px; }
    button { padding: 0.6rem 1rem; font-size: 16px; }
    .controls { margin-top: 0.8rem; }
    audio { margin-top: 1rem; width: 100%; }
  </style>
</head>
<body>
  <h2>Urdu to Speech AI</h2>
  <p>Enter Urdu text and press "Synthesize". Audio is generated on the server (requires ORATOR_API_KEY).</p>
  <textarea id="txt" placeholder="اپنا متن یہاں لکھیں..."></textarea>
  <div class="controls">
    <label for="voice">Voice:</label>
    <select id="voice">
      <option value="urdu" selected>urdu</option>
    </select>
    <button id="synth">Synthesize</button>
    <span id="status" style="margin-left:1rem;color:gray"></span>
  </div>
  <div id="player-area"></div>

  <script>
    const synthBtn = document.getElementById('synth');
    const txt = document.getElementById('txt');
    const voice = document.getElementById('voice');
    const status = document.getElementById('status');
    const playerArea = document.getElementById('player-area');

    synthBtn.addEventListener('click', async () => {
      const text = txt.value.trim();
      if (!text) { alert("Please enter text"); return; }
      status.textContent = "Generating...";
      synthBtn.disabled = true;
      playerArea.innerHTML = "";
      try {
        const res = await fetch('/api/synthesize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, voice: voice.value, format: 'mp3' })
        });
        if (!res.ok) {
          const err = await res.json().catch(()=>null) || {detail: await res.text()};
          throw new Error(JSON.stringify(err));
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = document.createElement('audio');
        audio.controls = true;
        audio.src = url;
        playerArea.appendChild(audio);

        // Download link
        const dl = document.createElement('a');
        dl.href = url;
        dl.download = 'urdu_tts.mp3';
        dl.textContent = 'Download audio';
        dl.style.display = 'inline-block';
        dl.style.marginLeft = '1rem';
        playerArea.appendChild(dl);

      } catch (e) {
        console.error(e);
        alert('Synthesis failed: ' + e.message);
      } finally {
        status.textContent = "";
        synthBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=INDEX_HTML, status_code=200)


async def fetch_binary_url(client: httpx.AsyncClient, url: str) -> bytes:
    # Fetch a remote audio URL and return bytes
    try:
        r = await client.get(url, timeout=60.0)
        r.raise_for_status()
        return r.content
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch audio URL: {str(e)}")


async def call_orator_tts_bytes(text: str, voice: str = "urdu", fmt: str = "mp3") -> (bytes, str):
    """
    Call Orator TTS and return (bytes, mime_type)
    This function tries to handle:
      - direct binary audio responses
      - JSON with base64 audio
      - JSON with "url" pointing to audio file
    Adjust payload/headers for your Orator API shape.
    """
    if not ORATOR_API_KEY:
        raise HTTPException(status_code=500, detail="Server is not configured with ORATOR_API_KEY")

    headers = {
        "Authorization": f"Bearer {ORATOR_API_KEY}",
        "Accept": "application/octet-stream, audio/*, application/json, */*",
        "Content-Type": "application/json",
    }

    payload = {
        "text": text,
        "voice": voice,
        "format": fmt
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(ORATOR_TTS_ENDPOINT, json=payload, headers=headers, timeout=60.0)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Request to Orator failed: {str(e)}")

        # If direct audio binary returned (content-type audio/...)
        content_type = resp.headers.get("Content-Type", resp.headers.get("content-type", ""))
        if resp.status_code == 200 and content_type and ("audio" in content_type or "octet-stream" in content_type):
            return resp.content, content_type.split(";")[0]

        # If JSON response, examine fields
        ct = resp.headers.get("Content-Type", "")
        if "application/json" in ct or (resp.text and (resp.text.strip().startswith("{") or resp.text.strip().startswith("["))):
            try:
                j = resp.json()
            except Exception:
                j = None
            # 1) base64 field
            if isinstance(j, dict):
                # common keys: audio, audio_base64, result, data
                for key in ("audio", "audio_base64", "result", "data"):
                    if key in j and isinstance(j[key], str):
                        b64 = j[key]
                        try:
                            b = base64.b64decode(b64)
                            # guess mime from format or extension
                            mime = "audio/mpeg" if fmt in ("mp3", "mpeg") else "audio/wav"
                            return b, mime
                        except Exception:
                            pass
                # 2) url pointing to audio
                for key in ("url", "audio_url", "result_url", "file"):
                    if key in j and isinstance(j[key], str) and j[key].startswith("http"):
                        audio_bytes = await fetch_binary_url(client, j[key])
                        # try to get mime via HEAD or from response headers already fetched
                        # (we fetched via GET in fetch_binary_url, so no extra step here)
                        # Assume mp3 if unknown
                        return audio_bytes, "audio/mpeg"
                # 3) nested structure sometimes has base64 at j["data"]["audio"]
                data = j.get("data")
                if isinstance(data, dict):
                    for key in ("audio", "audio_base64"):
                        if key in data and isinstance(data[key], str):
                            try:
                                b = base64.b64decode(data[key])
                                mime = "audio/mpeg" if fmt in ("mp3", "mpeg") else "audio/wav"
                                return b, mime
                            except Exception:
                                pass

        # If nothing matched but we have content, return raw bytes
        if resp.content:
            # fallback mime
            fallback_mime = "application/octet-stream"
            return resp.content, fallback_mime

        # Otherwise treat as error
        raise HTTPException(status_code=502, detail={"status_code": resp.status_code, "text": resp.text})


@app.post("/api/synthesize")
async def api_synthesize(payload: dict):
    """
    Request JSON:
      { "text": "...", "voice": "urdu", "format": "mp3" }
    Response: audio binary with appropriate content-type.
    """
    text = payload.get("text") if isinstance(payload, dict) else None
    voice = payload.get("voice", "urdu") if isinstance(payload, dict) else "urdu"
    fmt = payload.get("format", "mp3") if isinstance(payload, dict) else "mp3"

    if not text:
        return JSONResponse(status_code=400, content={"detail": "text is required"})

    # run the Orator call
    audio_bytes, mime = await call_orator_tts_bytes(text=text, voice=voice, fmt=fmt)

    # Stream the bytes back to client
    return StreamingResponse(io.BytesIO(audio_bytes), media_type=mime)


# Basic health endpoint
@app.get("/health")
async def health():
    ok = bool(ORATOR_API_KEY)
    return {"status": "ok" if ok else "missing_api_key", "orator_configured": ok}


if __name__ == "__main__":
    import uvicorn
    # uvicorn main:app --host 0.0.0.0 --port 7860
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 7860)), reload=False)
