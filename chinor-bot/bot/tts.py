"""O'zbekcha diktor ovozi (TTS) — rolik uchun.

Nima uchun Gemini: o'zbek tilida gapiradigan bepul xizmat kam. Piper'da
uz_UZ ovozi yo'q, Cloudflare va Groq TTS'lari faqat ingliz/ispan/arab
tillarida, Azure esa alohida kalit talab qiladi. Gemini TTS kaliti bizda
allaqachon bor va o'zbekcha lotin yozuvini tushunarli o'qiydi (2026-08-24
da sinab ko'rilgan).

Javob — 24 kHz mono PCM (`audio/L16`), sarlavhasiz. Shuning uchun WAV
sarlavhasini o'zimiz yozamiz.

Bloklovchi (tarmoqqa chiqadi): chaqiruvchi uni executor'da ishlatsin.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.request
import wave

logger = logging.getLogger(__name__)

MODEL = os.getenv("REELS_TTS_MODEL", "gemini-2.5-flash-preview-tts").strip()
VOICE = os.getenv("REELS_TTS_VOICE", "Kore").strip()      # Gemini ovoz nomi
SR = 24000
_TIMEOUT = 90

_API = ("https://generativelanguage.googleapis.com/v1beta/models/"
        "{model}:generateContent?key={key}")

# Ohang ko'rsatmasi matn bilan BIRGA yuboriladi (Gemini TTS shunday
# ishlaydi: uslub alohida maydon emas, so'rovning bir qismi).
_STYLE = ("Quyidagi gapni o'zbek tilida, do'stona va ishonarli reklama "
          "ohangida, shoshilmasdan o'qing. Faqat gapning o'zini o'qing:\n")


def available() -> bool:
    try:
        from bot import ai_engine
        return bool(ai_engine._keys("GEMINI_API_KEY"))
    except Exception:
        return False


def speak_dialog(lines: list, out_wav: str, voices: dict, style: str = "") -> bool:
    """Ikki kishilik suhbat — har biri O'Z ovozida (Gemini multi-speaker).

    `lines` — [(ism, gap)], `voices` — {ism: Gemini ovoz nomi}, aynan 2 ta.
    Gaplar orasida pauza bo'ladi (vaqtlarni keyin jimlikdan topamiz)."""
    names = list(voices)
    text = "\n".join(f"{n}: {' '.join(t.split())}" for n, t in lines if t.strip())
    if not text or len(names) != 2:
        return False
    cfg = {"multiSpeakerVoiceConfig": {"speakerVoiceConfigs": [
        {"speaker": n, "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voices[n]}}}
        for n in names]}}
    return speak(text, out_wav, style=style or (
        "Quyidagi kulgili suhbatni o'zbek tilida, multfilm qahramonlaridek jonli o'qing. "
        "Har gapdan keyin qisqa pauza qiling. Faqat gaplarni o'qing:\n"), speech_config=cfg)


def speak(text: str, out_wav: str, style: str = "", speech_config: dict = None) -> bool:
    """Matnni o'qib, WAV qilib saqlaydi. Muvaffaqiyatli bo'lsa True.
    `style` — o'z ohang ko'rsatmasi (bo'lmasa standart reklama ohangi).
    Qator bo'linishlari saqlanadi: diktor ularda pauza qiladi."""
    text = "\n".join(" ".join(l.split()) for l in (text or "").splitlines() if l.strip())
    if not text:
        return False
    body = json.dumps({
        "contents": [{"parts": [{"text": (style or _STYLE) + text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": speech_config or {"voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": VOICE}}},
        },
    }).encode()

    def _once(key: str) -> bytes:
        req = urllib.request.Request(
            _API.format(model=MODEL, key=key), data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.load(r)
        # Model ba'zan javobni OVOZSIZ qaytaradi (`content` yo'q yoki
        # parts ichida `inlineData` yo'q) — ayniqsa qisqa gaplarda.
        # Bu tarmoq xatosi emas, shuning uchun pastda qayta uriniladi.
        for cand in d.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                blob = part.get("inlineData") or part.get("inline_data")
                if blob and blob.get("data"):
                    return base64.b64decode(blob["data"])
        raise RuntimeError("ovoz qaytmadi (%s)" %
                           ((d.get("candidates") or [{}])[0]
                            .get("finishReason") or "sabab noma'lum"))

    raw = b""
    for attempt in (1, 2, 3):
        try:
            from bot import ai_engine
            raw = ai_engine._call_with_keys("GEMINI_API_KEY", _once)
            break
        except Exception as e:
            logger.info("🔊 Ovoz yozilmadi (%s, %d-urinish): %s",
                        text[:30], attempt, e)
    if not raw:
        return False
    try:
        with wave.open(out_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(raw)
    except Exception as e:
        logger.info("🔊 WAV saqlanmadi: %s", e)
        return False
    return os.path.getsize(out_wav) > 2000


def duration(path: str) -> float:
    """WAV uzunligi (soniya). O'qib bo'lmasa 0."""
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or SR)
    except Exception:
        return 0.0
