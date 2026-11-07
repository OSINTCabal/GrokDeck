"""Speech-to-text and text-to-speech through the xAI voice API.

The credential stays in this process. Callers get transcript text or audio
bytes. Error text never includes the credential.
"""

from __future__ import annotations

import json

import httpx

from sidecar import secrets, usage, xai


class VoiceError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _credential() -> tuple[str, dict[str, str]]:
    key = secrets.get_key()
    if key:
        return key, {"Authorization": f"Bearer {key}"}
    token = usage._bearer()
    if token:
        return token, {
            "Authorization": "Bearer " + token,
            "X-XAI-Token-Auth": "xai-grok-cli",
        }
    raise VoiceError(401, "Voice needs you signed in, or an xAI API key in Settings.")


def _reject(status: int, secret: str, body: bytes) -> None:
    if secret and secret.encode("utf-8") in body:
        body = b""
    del body
    if status == 401:
        message = "Voice was rejected for this account."
    else:
        message = "Voice request was rejected."
    raise VoiceError(status if status < 500 else 502, message)


def _filename(mime: str) -> str:
    lower = mime.lower()
    if "webm" in lower:
        return "speech.webm"
    if "mp4" in lower or "m4a" in lower:
        return "speech.m4a"
    if "ogg" in lower:
        return "speech.ogg"
    if "wav" in lower:
        return "speech.wav"
    if "mpeg" in lower or "mp3" in lower:
        return "speech.mp3"
    return "speech.webm"


async def transcribe(data: bytes, mime: str) -> str:
    secret, headers = _credential()
    form = {"model": "grok-voice-transcribe-2.0", "language": "en", "format": "true"}
    files = {"file": (_filename(mime), data, mime or "application/octet-stream")}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(f"{xai.API}/stt", headers=headers, data=form, files=files)
    if response.status_code >= 400:
        _reject(response.status_code, secret, response.content)
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise VoiceError(502, "Voice request was rejected.") from exc
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise VoiceError(502, "No speech was heard.")
    return text.strip()


async def synthesize(text: str) -> tuple[bytes, str]:
    spoken = " ".join(text.split()).strip()
    if not spoken:
        raise VoiceError(400, "Nothing to speak.")
    spoken = spoken[:1500]
    secret, headers = _credential()
    headers = {**headers, "Content-Type": "application/json"}
    payload = {
        "text": spoken,
        "voice_id": "ara",
        "language": "en",
        "output_format": {"codec": "mp3", "sample_rate": 24000, "bit_rate": 128000},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(f"{xai.API}/tts", headers=headers, json=payload)
    if response.status_code >= 400:
        _reject(response.status_code, secret, response.content)
    ctype = (response.headers.get("content-type") or "audio/mpeg").split(";", 1)[0].strip()
    if "json" in ctype or not response.content:
        raise VoiceError(502, "Voice request was rejected.")
    return response.content, ctype
