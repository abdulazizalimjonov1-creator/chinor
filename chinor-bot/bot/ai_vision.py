"""Rasmdan tavsif yozish — ko'p provayderli, o'zi tiklanadigan zanjir.

Nima uchun kerak: 2026-06 da Groq, 2026-08 da uning modeli o'ldi, Gemini'ning
bepul kvotasi ham nolga tushdi. Har safar tanaffus **jimgina** bo'lgan — kod
xatoni logga yozib, postni tavsifsiz yuboraverardi, shuning uchun buzilgani
haftalab sezilmagan. Bu modul shu ikkala muammoni yopadi:

  1. **O'tib ketish.** Zanjirdagi model yiqilsa, keyingisiga o'tadi. Ishlagani
     eslab qolinadi va keyingi safar birinchi bo'lib sinaladi.
  2. **Sovish (cooldown).** Yiqilgan model ma'lum vaqt chetlab o'tiladi —
     har postda o'lik modelni kutib o'tirmaydi. Xato turiga qarab:
       • model yo'q (404)  → 24 soat (u tez qaytmaydi)
       • kvota (429)       → 1 soat (ertaga tiklanishi mumkin)
       • tarmoq/boshqa     → 5 daqiqa
  3. **Oxirgi chora.** Hamma rasm modeli yiqilsa, mahsulot NOMIDAN matnli
     tavsif yoziladi. Rasmli tavsifchalik yaxshi emas, lekin bo'shdan afzal.
  4. **Jim qolmaslik.** Hammasi yiqilsa admin'ga Telegram xabar ketadi
     (6 soatda bir marta, spam bo'lmasin).

Zanjirni `.env` dan o'zgartirish mumkin. Format: `provayder:model`, vergul
bilan. Model nomida ikki nuqta bo'lsa ham ishlaydi (birinchisi bo'yicha
ajratiladi), masalan `openrouter:qwen/qwen-2.5-vl-7b-instruct:free`.

    AI_VISION_CHAIN=gemini:gemini-2.5-flash,openrouter:...:free
    AI_TEXT_CHAIN=groq:llama-3.3-70b-versatile,groq:openai/gpt-oss-120b

YANGI BEPUL PROVAYDER QO'SHISH: kalitni `.env` ga yozib, zanjirga bir bo'lak
qo'shsangiz kifoya — kodga tegish shart emas. Qo'llab-quvvatlanadiganlar
`_OPENAI_COMPAT` ro'yxatida; hammasi OpenAI formatida ishlaydi. Kaliti yo'q
provayder JIM o'tkazib yuboriladi, xato hisoblanmaydi.
"""

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class _NoKey(Exception):
    """Provayder kaliti sozlanmagan — xato emas, shunchaki o'tkazib yuboriladi."""

# 2026-08-04 da VPS'dan haqiqiy rasm bilan sinalgan va ISHLAGAN modellar.
# (gemini-2.0-flash va -lite — kvota 0, 429; gemini-2.5-flash-lite — 404.)
_DEFAULT_CHAIN = (
    "gemini:gemini-2.5-flash,"
    "gemini:gemini-3.5-flash-lite,"
    "gemini:gemini-3.1-flash-lite,"
    "gemini:gemini-flash-lite-latest,"
    # Kalit qo'shilsa o'zi ishga tushadi, bo'lmasa jim o'tkazib yuboriladi:
    "openrouter:qwen/qwen2.5-vl-72b-instruct:free,"
    "openrouter:meta-llama/llama-3.2-11b-vision-instruct:free,"
    "github:gpt-4o-mini,"
    "mistral:pixtral-12b-2409,"
    "cloudflare:@cf/llava-hf/llava-1.5-7b-hf"
)

# Rasm modellari tugasa — mahsulot NOMIDAN matnli tavsif. Groq bepul va
# tez, vision'i yo'q lekin matni bor, shuning uchun aynan shu yerda asqotadi.
# 2026-08-04 da sinalgan: ikkalasi ham ikki tilli tavsif yozib berdi.
_DEFAULT_TEXT_CHAIN = (
    "groq:llama-3.3-70b-versatile,"
    "groq:openai/gpt-oss-120b,"
    "groq:llama-3.1-8b-instant,"
    "gemini:gemini-2.5-flash,"
    "gemini:gemini-flash-lite-latest,"
    "openrouter:meta-llama/llama-3.3-70b-instruct:free"
)

# OpenAI formatidagi provayderlar: nomi → (manzil, kalit o'zgaruvchisi).
# Yangisini qo'shish uchun shu yerga bitta qator yetadi.
_OPENAI_COMPAT = {
    "groq":       ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "github":     ("https://models.inference.ai.azure.com/chat/completions", "GITHUB_TOKEN"),
    "mistral":    ("https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY"),
    "together":   ("https://api.together.xyz/v1/chat/completions", "TOGETHER_API_KEY"),
    "deepinfra":  ("https://api.deepinfra.com/v1/openai/chat/completions", "DEEPINFRA_API_KEY"),
}

# Cloudflare oddiy `Python-urllib` UA'sini bloklaydi (xato 1010) — brauzer
# UA'si VPS'dan o'tishi tekshirilgan.
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")

_COOLDOWN_GONE = 24 * 3600    # model yo'q
_COOLDOWN_QUOTA = 3600        # kvota tugagan
_COOLDOWN_OTHER = 300         # tarmoq va boshqalar
_ALERT_EVERY = 6 * 3600       # admin'ga xabar oralig'i

_TIMEOUT = 45


def _state_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.getenv("DATA_DIR") or os.path.join(os.path.dirname(root), "data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        data_dir = "/tmp"
    return os.path.join(data_dir, "ai_vision_state.json")


def _load_state() -> dict:
    try:
        with open(_state_path(), "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    try:
        with open(_state_path(), "w") as f:
            json.dump(st, f)
    except OSError:
        pass


def _parse(raw: str) -> list:
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        provider, model = part.split(":", 1)   # model ichida ":" bo'lishi mumkin
        out.append((provider.strip().lower(), model.strip()))
    return out


def _chain() -> list:
    """Rasmli tavsif zanjiri."""
    out = _parse(os.getenv("AI_VISION_CHAIN", "").strip() or _DEFAULT_CHAIN)
    gv = os.getenv("GROQ_VISION_MODEL", "").strip()
    if gv:
        out.append(("groq", gv))
    return out


def _text_chain() -> list:
    """Rasmsiz (nomdan) tavsif zanjiri — rasm modellari tugaganda."""
    return _parse(os.getenv("AI_TEXT_CHAIN", "").strip() or _DEFAULT_TEXT_CHAIN)


def _cooldown_for(err: Exception) -> int:
    txt = str(err).lower()
    if isinstance(err, urllib.error.HTTPError):
        if err.code == 404:
            return _COOLDOWN_GONE
        if err.code == 429:
            return _COOLDOWN_QUOTA
    if "not found" in txt or "does not exist" in txt:
        return _COOLDOWN_GONE
    if "quota" in txt or "exceeded" in txt or "resource_exhausted" in txt:
        return _COOLDOWN_QUOTA
    return _COOLDOWN_OTHER


# ── Provayderlar (SDK ishlatilmaydi — to'g'ridan-to'g'ri REST) ──────────────

def _call_gemini(model: str, prompt: str, img_b64: str = "") -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY yo'q")
    parts = []
    if img_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})
    parts.append({"text": prompt})
    body = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {"maxOutputTokens": 400},
    }).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        data=body, headers={"Content-Type": "application/json", "User-Agent": _UA},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        d = json.load(r)
    return (d["candidates"][0]["content"]["parts"][0]["text"] or "").strip()


def _call_openai_compat(provider: str, model: str, prompt: str, img_b64: str = "") -> str:
    """OpenAI formatidagi har qanday provayder (groq, openrouter, github, ...)."""
    url, key_env = _OPENAI_COMPAT[provider]
    key = os.getenv(key_env, "").strip()
    if not key:
        raise _NoKey(f"{key_env} yo'q")
    if img_b64:
        content = [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            {"type": "text", "text": prompt},
        ]
    else:
        content = prompt
    body = json.dumps({
        "model": model, "max_tokens": 400,
        "messages": [{"role": "user", "content": content}],
    }).encode()
    headers = {"Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "User-Agent": _UA}
    if provider == "openrouter":
        # OpenRouter bepul darajada shuni so'raydi.
        headers["HTTP-Referer"] = "https://chinorpos.com"
        headers["X-Title"] = "Chinor POS"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        d = json.load(r)
    return (d["choices"][0]["message"]["content"] or "").strip()


def _call_cloudflare(model: str, prompt: str, img_b64: str = "") -> str:
    """Cloudflare Workers AI — o'z formati bor, OpenAI'ga mos emas."""
    acc = os.getenv("CF_ACCOUNT_ID", "").strip()
    key = os.getenv("CF_API_TOKEN", "").strip()
    if not acc or not key:
        raise _NoKey("CF_ACCOUNT_ID/CF_API_TOKEN yo'q")
    payload = {"prompt": prompt, "max_tokens": 400}
    if img_b64:
        payload["image"] = list(base64.b64decode(img_b64))
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "User-Agent": _UA},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        d = json.load(r)
    return (d.get("result", {}).get("description")
            or d.get("result", {}).get("response") or "").strip()


def _dispatch(provider: str, model: str, prompt: str, img_b64: str = "") -> str:
    if provider == "gemini":
        return _call_gemini(model, prompt, img_b64)
    if provider == "cloudflare":
        return _call_cloudflare(model, prompt, img_b64)
    if provider in _OPENAI_COMPAT:
        return _call_openai_compat(provider, model, prompt, img_b64)
    raise _NoKey(f"noma'lum provayder: {provider}")




# ── Asosiy funksiya ────────────────────────────────────────────────────────

def describe(fpath: str, prompt: str, product_name: str = "") -> str:
    """Zanjir bo'ylab urinib, birinchi ishlagan tavsifni qaytaradi.

    Bloklovchi (sinxron) funksiya — chaqiruvchi uni executor'da ishlatadi.
    Hech biri ishlamasa '' qaytaradi (post baribir ketaveradi).
    """
    try:
        with open(fpath, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
    except OSError as e:
        logger.warning("🤖 Rasm o'qilmadi: %s", e)
        img_b64 = ""

    st = _load_state()
    cooldown = st.get("cooldown", {})
    now = time.time()

    chain = _chain()
    # Oxirgi ishlagani birinchi sinaladi — odatda u yana ishlaydi.
    last_good = st.get("last_good")
    if last_good:
        chain.sort(key=lambda pm: 0 if f"{pm[0]}:{pm[1]}" == last_good else 1)

    tried, skipped = [], []
    for provider, model in chain:
        tag = f"{provider}:{model}"
        until = cooldown.get(tag, 0)
        if until > now:
            skipped.append(f"{tag} ({int((until - now) / 60)}daq)")
            continue
        try:
            desc = _dispatch(provider, model, prompt, img_b64)
            if desc:
                st["last_good"] = tag
                cooldown.pop(tag, None)
                st["cooldown"] = cooldown
                _save_state(st)
                logger.info("🤖 %s tavsif: %s...", tag, desc[:70])
                return desc
            raise RuntimeError("bo'sh javob")
        except _NoKey:
            continue          # kaliti yo'q — xato emas, keyingisiga
        except Exception as e:
            secs = _cooldown_for(e)
            cooldown[tag] = now + secs
            tried.append(f"{tag} → {type(e).__name__} ({secs // 60}daq sovish)")
            logger.warning("🤖 %s yiqildi: %s", tag, str(e)[:160])

    st["cooldown"] = cooldown
    _save_state(st)

    # ── Oxirgi chora: rasmsiz, faqat nomdan ───────────────────────────────
    if product_name:
        for provider, model in _text_chain():
            if cooldown.get(f"text:{provider}:{model}", 0) > now:
                continue
            try:
                desc = _dispatch(
                    provider, model,
                    f"{prompt}\n\nMahsulot nomi: {product_name}", "")
                if desc:
                    logger.info("🤖 %s:%s — rasmsiz, nomdan tavsif berdi",
                                provider, model)
                    return desc
            except _NoKey:
                continue
            except Exception as e:
                cooldown[f"text:{provider}:{model}"] = now + _cooldown_for(e)
                tried.append(f"text {provider}:{model} → {type(e).__name__}")
        st["cooldown"] = cooldown
        _save_state(st)

    logger.error("🤖 HAMMA AI yiqildi. Urinildi: %s | Sovishda: %s",
                 "; ".join(tried) or "-", "; ".join(skipped) or "-")
    _alert_admin(tried, skipped)
    return ""


def _alert_admin(tried: list, skipped: list) -> None:
    """Hammasi yiqilganda admin'ga xabar — 6 soatda bir marta.

    Eng muhim qism: ilgari nosozlik JIM bo'lgan, shuning uchun haftalab
    sezilmagan. Endi seziladi.
    """
    st = _load_state()
    now = time.time()
    if now - float(st.get("last_alert", 0)) < _ALERT_EVERY:
        return

    token = os.getenv("BOT_TOKEN", "").strip()
    chat = os.getenv("AI_ALERT_CHAT_ID", os.getenv("GLAVNIY_ADMIN_ID", "")).strip()
    if not token or not chat:
        return

    text = ("⚠️ <b>AI tavsif ishlamayapti</b>\n\n"
            "Kanal postlari tavsifsiz ketmoqda. Barcha modellar yiqildi:\n\n"
            + ("\n".join(f"• {t}" for t in tried) or "—")
            + ("\n\nSovishda: " + ", ".join(skipped) if skipped else "")
            + "\n\nZanjirni .env dagi <code>AI_VISION_CHAIN</code> orqali "
              "o'zgartirish mumkin.")
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": text,
                             "parse_mode": "HTML"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=20).read()
        st["last_alert"] = now
        _save_state(st)
    except Exception as e:
        logger.warning("🤖 Ogohlantirish yuborilmadi: %s", e)


def status() -> dict:
    """Joriy holat — tekshirish uchun."""
    st = _load_state()
    now = time.time()
    cd = st.get("cooldown", {})
    def _has_key(prov: str) -> bool:
        if prov == "gemini":
            return bool(os.getenv("GEMINI_API_KEY", "").strip())
        if prov == "cloudflare":
            return bool(os.getenv("CF_ACCOUNT_ID", "").strip()
                        and os.getenv("CF_API_TOKEN", "").strip())
        env = _OPENAI_COMPAT.get(prov, (None, None))[1]
        return bool(env and os.getenv(env, "").strip())

    return {
        "vision": [f"{p}:{m}" + ("" if _has_key(p) else "  (kalit yo'q)")
                   for p, m in _chain()],
        "text": [f"{p}:{m}" + ("" if _has_key(p) else "  (kalit yo'q)")
                 for p, m in _text_chain()],
        "last_good": st.get("last_good"),
        "cooling": {k: int((v - now) / 60) for k, v in cd.items() if v > now},
    }
