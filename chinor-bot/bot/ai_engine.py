"""AI dvigateli — ko'p provayderli, o'zi tiklanadigan zanjir.

Ikki ish qiladi: rasmdan tavsif (`describe`) va matnli suhbat (`chat`) —
mijoz bilan gaplashish, mahsulot tavsiya qilish, analitika. Ikkalasi ham
bir xil zanjir, sovish va ogohlantirish mexanizmidan foydalanadi.

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
import threading
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
# 2026-10-22: Groq kaliti o'lik (401) — oxiriga surildi; Cloudflare Workers AI
# bepul mustaqil zaxira sifatida Gemini'dan keyin turadi (Gemini daqiqalik
# limitga urilganda javob shu yerdan keladi).
_DEFAULT_TEXT_CHAIN = (
    "gemini:gemini-2.5-flash,"
    "gemini:gemini-flash-lite-latest,"
    "cloudflare:@cf/meta/llama-3.3-70b-instruct-fp8-fast,"
    "openrouter:meta-llama/llama-3.3-70b-instruct:free,"
    "groq:llama-3.3-70b-versatile,"
    "groq:openai/gpt-oss-120b"
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


def _bump(st: dict, tag: str) -> None:
    """Kunlik hisoblagich — qaysi model qancha ishlatilganini bilish uchun.

    Bepul darajalarning kunlik limiti bor. Bu hisoblagichsiz "yetadimi yoki
    yetmaydimi" degan savolga faqat taxmin bilan javob berish mumkin edi.
    """
    day = time.strftime("%Y-%m-%d")
    u = st.setdefault("usage", {})
    if u.get("day") != day:
        u.clear()
        u["day"] = day
    u[tag] = int(u.get(tag, 0)) + 1


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

def _keys(env_name: str) -> list:
    """Bitta provayderning BIR NECHTA kaliti.

    Bepul kvota kalitga bog'liq: ikkinchi akkaunt = ikkinchi kvota. Shuning
    uchun kalitlar ro'yxat bo'lib o'qiladi:
        GEMINI_API_KEY=birinchi
        GEMINI_API_KEY_2=ikkinchi          (…_3, …_4 ham bo'ladi)
    yoki bitta o'zgaruvchida vergul bilan: GEMINI_API_KEY=bir,ikki
    """
    out = []
    for name in [env_name] + [f"{env_name}_{i}" for i in range(2, 10)]:
        for k in (os.getenv(name, "") or "").split(","):
            k = k.strip()
            if k and k not in out:
                out.append(k)
    return out


def _call_with_keys(env_name: str, fn):
    """Kalitlarni navbat bilan sinaydi: biri kvotani tugatgan bo'lsa keyingisi.

    Har kalit ALOHIDA sovish muddatiga ega (`key:<env>:<n>`) — kvotasi tugagani
    bir soat chetlab o'tiladi, qolganlari ishlayveradi. Hammasi sovishda
    bo'lsa ham OXIRGISI baribir sinaladi: sovish muddati taxmin, aniq emas.
    """
    keys = _keys(env_name)
    if not keys:
        raise _NoKey(f"{env_name} yo'q")
    st = _load_state()
    cooldown = st.get("cooldown", {})
    now = time.time()
    live = [(i, k) for i, k in enumerate(keys, 1)
            if cooldown.get(f"key:{env_name}:{i}", 0) <= now]
    if not live:
        live = [(len(keys), keys[-1])]

    last = None
    for i, key in live:
        try:
            return fn(key)
        except Exception as e:
            last = e
            secs = _cooldown_for(e)
            # Faqat kvota/limit uchun kalitni chetlab o'tamiz. Model yo'qligi
            # yoki tarmoq xatosi kalitga bog'liq emas — boshqa kalit ham
            # xuddi shunday yiqiladi, urinib o'tirish vaqtni yeydi.
            if secs == _COOLDOWN_QUOTA and len(live) > 1:
                cooldown[f"key:{env_name}:{i}"] = now + secs
                st["cooldown"] = cooldown
                _save_state(st)
                logger.info("🔑 %s #%d kvotasi tugadi — keyingi kalitga", env_name, i)
                continue
            raise
    raise last


def _gemini_text(d: dict) -> str:
    """Gemini javobidan MATNni yig'adi.

    Faqat `parts[0]["text"]` ni olish yaramaydi: qidiruv (grounding) yoqilganda
    javob bir nechta bo'lakdan iborat bo'ladi va birinchisi matn bo'lmasligi
    mumkin — o'shanda KeyError chiqib, ishlaydigan model «yiqilgan» deb
    hisoblanardi.
    """
    out = []
    for cand in d.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            if part.get("text"):
                out.append(part["text"])
    return "\n".join(out).strip()


# Hujjat o'qish (yorliq/yo'riqnoma) rejimi: rasmdan UZUN matn kerak bo'ladi,
# oddiy tavsifning 800/400 tokenlik limiti yorliq matnini o'rtasidan kesadi.
# Oqimga bog'langan — parallel ishlayotgan boshqa tavsiflarga ta'sir qilmaydi.
_tls = threading.local()


def read_document(fpath: str, prompt: str, validate=None, max_tokens: int = 3000) -> str:
    """Rasmdagi hujjatni o'qiydi (OCR + tushunish). `describe` dan farqi:
    javob limiti katta va rasm modellari yiqilsa NOMDAN taxmin QILINMAYDI —
    yorliqdagi faktni o'ylab topish mumkin emas."""
    _tls.img_tokens = max_tokens
    try:
        return describe(fpath, prompt, validate=validate, allow_text_fallback=False)
    finally:
        _tls.img_tokens = 0


def _call_gemini(model: str, prompt: str, img_b64: str = "",
                 system: str = "", search: bool = False) -> str:
    parts = []
    if img_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})
    parts.append({"text": prompt})
    # Gemini 2.5+ "o'ylash" tokenlarini sarflaydi va ular ham shu limitdan
    # yeydi. Limit kichik bo'lsa javobga joy qolmay, model BO'SH qaytaradi
    # (thoughtsTokenCount 381, javob 0 — real holat). Shuning uchun matn
    # uchun limit kattaroq va o'ylash butunlay o'chiriladi: bizga mulohaza
    # emas, javob kerak.
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "maxOutputTokens": 4000 if not img_b64 else (getattr(_tls, "img_tokens", 0) or 800),
        },
    }
    # "O'ylash"ni o'chirish faqat 2.5 avlodida bor. Yangi modellar
    # (gemini-flash-lite-latest = 3.x, gemini-3.5-*) thinkingBudget=0 ni
    # "invalid argument" (400) deb rad etadi — 2026-10-20 da aynan shu sabab
    # zaxira model doim yiqilib, asosiy kvota tugaganda AI butunlay to'xtardi.
    if "2.5" in model:
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    if search:
        # Google qidiruvi (grounding): model javob yozishdan OLDIN internetdan
        # izlaydi. Aynan shu narsa tovar haqidagi ma'lumotni o'ylab topilgan
        # emas, HAQIQIY qilib beradi. Qidiruv paytida "o'ylash"ni o'chirib
        # bo'lmaydi — u yerda budjet berilmasa model bo'sh qaytaradi.
        payload["tools"] = [{"google_search": {}}]
        payload["generationConfig"].pop("thinkingConfig", None)
        # Limit KATTA: qidiruv paytida "o'ylash" tokenlari ham shu hisobdan
        # yeyiladi. 4000 da javob o'rtasidan kesilib, tayyor natija shaklga
        # mos kelmay qolardi — qidiruv kvotasi esa bekorga sarflanardi.
        payload["generationConfig"]["maxOutputTokens"] = 8000
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    body = json.dumps(payload).encode()

    def _once(key: str) -> str:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}",
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": _UA},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.load(r)
        return _gemini_text(d)

    return _call_with_keys("GEMINI_API_KEY", _once)


def _gemini_models() -> list:
    """Zanjirdagi gemini modellari (qidiruv faqat Gemini'da bor)."""
    out = []
    for provider, model in _text_chain() + _chain():
        if provider == "gemini" and model not in out:
            out.append(model)
    return out


def web_search(prompt: str, system: str = "", validate=None,
               timeout: float = None) -> str:
    """Internetdan (Google) izlab javob yozadi — Gemini grounding orqali.

    Oddiy `chat()` dan farqi: model o'z xotirasidan emas, QIDIRUV natijasidan
    yozadi. Do'kondagi tovar nomlari mahalliy va model ularni bilmasligi
    mumkin — internet esa biladi.

    Qidiruv har provayderda yo'q, shuning uchun zanjirdan faqat Gemini
    modellari sinaladi. Hech biri ishlamasa '' — chaqiruvchi boshqa yo'l
    (DuckDuckGo) bilan davom etadi. Bloklovchi: executor'da chaqiring.
    """
    global _TIMEOUT
    st = _load_state()
    cooldown = st.get("cooldown", {})
    now = time.time()
    saved = _TIMEOUT
    if timeout:
        _TIMEOUT = int(timeout)
    try:
        for model in _gemini_models():
            tag = f"search:gemini:{model}"
            if cooldown.get(tag, 0) > now:
                continue
            try:
                out = _call_gemini(model, prompt, "", system=system, search=True)
                if out and validate is not None and not validate(out):
                    raise RuntimeError("javob shakli mos emas")
                if out:
                    cooldown.pop(tag, None)
                    st["cooldown"] = cooldown
                    _save_state(st)
                    logger.info("🔎 Qidiruvli javob: %s", model)
                    return out
                raise RuntimeError("bo'sh javob")
            except _NoKey:
                return ""
            except Exception as e:
                cooldown[tag] = now + _cooldown_for(e)
                logger.warning("🔎 %s qidiruvi yiqildi: %s", tag, str(e)[:160])
        st["cooldown"] = cooldown
        _save_state(st)
        return ""
    finally:
        _TIMEOUT = saved


def _call_openai_compat(provider: str, model: str, prompt: str,
                        img_b64: str = "", system: str = "") -> str:
    """OpenAI formatidagi har qanday provayder (groq, openrouter, github, ...)."""
    url, key_env = _OPENAI_COMPAT[provider]
    if not _keys(key_env):
        raise _NoKey(f"{key_env} yo'q")
    if img_b64:
        content = [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            {"type": "text", "text": prompt},
        ]
    else:
        content = prompt
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": content})
    body = json.dumps({
        "model": model,
        "max_tokens": 1200 if not img_b64 else (getattr(_tls, "img_tokens", 0) or 400),
        "messages": msgs,
    }).encode()
    def _once(key: str) -> str:
        headers = {"Authorization": f"Bearer {key}",
                   "Content-Type": "application/json", "User-Agent": _UA}
        if provider == "openrouter":
            # OpenRouter bepul darajada shuni so'raydi.
            headers["HTTP-Referer"] = "https://chinorpos.com"
            headers["X-Title"] = "Chinor POS"
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.load(r)
        out = d.get("choices") or []
        if not out:
            # Ba'zi provayderlar 200 qaytarib, ichida xato yozadi.
            raise RuntimeError(str(d.get("error") or d)[:200])
        return (out[0]["message"]["content"] or "").strip()

    return _call_with_keys(key_env, _once)


def _call_cloudflare(model: str, prompt: str, img_b64: str = "") -> str:
    """Cloudflare Workers AI — o'z formati bor, OpenAI'ga mos emas."""
    acc = os.getenv("CF_ACCOUNT_ID", "").strip()
    if not acc or not _keys("CF_API_TOKEN"):
        raise _NoKey("CF_ACCOUNT_ID/CF_API_TOKEN yo'q")
    payload = {"prompt": prompt, "max_tokens": 400}
    if img_b64:
        payload["image"] = list(base64.b64decode(img_b64))
    data = json.dumps(payload).encode()

    def _once(key: str) -> str:
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}",
            data=data,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "User-Agent": _UA},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.load(r)
        return (d.get("result", {}).get("description")
                or d.get("result", {}).get("response") or "").strip()

    return _call_with_keys("CF_API_TOKEN", _once)


def _dispatch(provider: str, model: str, prompt: str, img_b64: str = "") -> str:
    if provider == "gemini":
        return _call_gemini(model, prompt, img_b64)
    if provider == "cloudflare":
        return _call_cloudflare(model, prompt, img_b64)
    if provider in _OPENAI_COMPAT:
        return _call_openai_compat(provider, model, prompt, img_b64)
    raise _NoKey(f"noma'lum provayder: {provider}")




# ── Matnli suhbat (mijoz bilan gaplashish, analitika, tavsiya) ─────────────

def chat(prompt: str, system: str = "", timeout: float = None,
         validate=None) -> str:
    """Matnli so'rov — matn zanjiri bo'ylab, birinchi ishlagani qaytariladi.

    `validate(javob) -> bool` berilsa, javob SHAKLI ham tekshiriladi: mos
    kelmasa model yiqilgan hisoblanadi va zanjir keyingisiga o'tadi.

    Nima uchun kerak: kuchsiz bepul modellar ba'zan bo'sh emas, lekin
    ma'nosiz matn qaytaradi (2026-08-05: bitta model "Kategoriya nomlaridan
    bitta yoz…" degan jumlani o'nlab marta takrorlab yubordi). Bunday javob
    "muvaffaqiyat" deb qabul qilinsa, zanjir hech qachon ishlaydigan modelga
    o'tmaydi.

    Bloklovchi. Chaqiruvchi executor'da ishlatadi. Hech biri ishlamasa ''.
    """
    st = _load_state()
    cooldown = st.get("cooldown", {})
    now = time.time()

    chain = _text_chain()   # har safar boshidan — yuqoridagi izohga qarang

    tried = []
    for provider, model in chain:
        tag = f"text:{provider}:{model}"
        if cooldown.get(tag, 0) > now:
            continue
        try:
            out = _dispatch_text(provider, model, prompt, system, timeout)
            if out and validate is not None and not validate(out):
                raise RuntimeError("javob shakli mos emas")
            if out:
                st["last_good_text"] = f"{provider}:{model}"
                _bump(st, tag)
                cooldown.pop(tag, None)
                st["cooldown"] = cooldown
                _save_state(st)
                return out
            raise RuntimeError("bo'sh javob")
        except _NoKey:
            continue
        except Exception as e:
            cooldown[tag] = now + _cooldown_for(e)
            tried.append(f"{tag} → {type(e).__name__}")
            logger.warning("🤖 %s yiqildi: %s", tag, str(e)[:160])

    st["cooldown"] = cooldown
    _save_state(st)
    logger.error("🤖 Matn AI: hamma provayder yiqildi. %s", "; ".join(tried) or "-")
    _alert_admin(tried, [])
    return ""


def _dispatch_text(provider: str, model: str, prompt: str,
                   system: str = "", timeout: float = None) -> str:
    """Matn chaqiruvi — system ko'rsatmasi provayderga qarab joylashtiriladi."""
    global _TIMEOUT
    saved = _TIMEOUT
    if timeout:
        _TIMEOUT = int(timeout)
    try:
        if provider == "gemini":
            return _call_gemini(model, prompt, "", system=system)
        if provider == "cloudflare":
            full = f"{system}\n\n{prompt}" if system else prompt
            return _call_cloudflare(model, full, "")
        if provider in _OPENAI_COMPAT:
            return _call_openai_compat(provider, model, prompt, "", system=system)
        raise _NoKey(f"noma'lum provayder: {provider}")
    finally:
        _TIMEOUT = saved


# ── Asosiy funksiya ────────────────────────────────────────────────────────

def describe(fpath: str, prompt: str, product_name: str = "",
             validate=None, allow_text_fallback: bool = True) -> str:
    """Zanjir bo'ylab urinib, birinchi ishlagan tavsifni qaytaradi.

    `validate(javob) -> bool` berilsa, javob SHAKLI ham tekshiriladi: mos
    kelmasa model yiqilgan hisoblanadi va zanjir keyingisiga o'tadi.
    Nima uchun: kuchsiz model ba'zan KO'RSATMANING O'ZINI qaytaradi
    (2026-08-05 da kanalga "…o'zbekcha 1 ta qisqa tavsif yoz" degan matn
    tavsif o'rnida chiqib ketdi). Bunday javob muvaffaqiyat emas.

    `allow_text_fallback=False` — rasm modellari yiqilsa NOMDAN yozishga
    o'tilmaydi, '' qaytariladi. Kanal tavsifi uchun aynan shu kerak:
    nomdan yozilgan tavsif ko'pincha noto'g'ri chiqadi ("Shisha choynak"ni
    ko'rmagan model unga "sifatli asbob" deb yozadi), tavsifsiz post esa
    hech kimni chalg'itmaydi.

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

    # ZANJIR HAR SAFAR BOSHIDAN aylanadi. Ilgari "oxirgi ishlagani birinchi"
    # qoidasi bor edi — u tezlik uchun edi, lekin zanjirni qaytmaydigan qilib
    # qo'yardi: 1-model kvotasi tugab 3-modelga o'tsak, ertaga 1-model
    # tiklansa ham tizim 3-modelda qolib ketardi. Endi sovish muddati
    # o'lik modellarni o'zi chetlab o'tadi (tarmoqqa chiqmasdan), shuning
    # uchun boshidan boshlash tekin — va kvota tiklanishi bilan eng yaxshi
    # modelga avtomatik qaytiladi.
    chain = _chain()

    tried, skipped = [], []
    for provider, model in chain:
        tag = f"{provider}:{model}"
        until = cooldown.get(tag, 0)
        if until > now:
            skipped.append(f"{tag} ({int((until - now) / 60)}daq)")
            continue
        try:
            desc = _dispatch(provider, model, prompt, img_b64)
            if desc and validate is not None and not validate(desc):
                raise RuntimeError("javob shakli mos emas")
            if desc:
                st["last_good"] = tag
                _bump(st, tag)
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
    if product_name and allow_text_fallback:
        for provider, model in _text_chain():
            if cooldown.get(f"text:{provider}:{model}", 0) > now:
                continue
            try:
                desc = _dispatch(
                    provider, model,
                    f"{prompt}\n\nMahsulot nomi: {product_name}", "")
                if desc and validate is not None and not validate(desc):
                    raise RuntimeError("javob shakli mos emas")
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
    def _nkeys(prov: str) -> int:
        if prov == "gemini":
            return len(_keys("GEMINI_API_KEY"))
        if prov == "cloudflare":
            return (len(_keys("CF_API_TOKEN"))
                    if os.getenv("CF_ACCOUNT_ID", "").strip() else 0)
        env = _OPENAI_COMPAT.get(prov, (None, None))[1]
        return len(_keys(env)) if env else 0

    def _mark(prov: str) -> str:
        n = _nkeys(prov)
        if not n:
            return "  (kalit yo'q)"
        return f"  ({n} kalit)" if n > 1 else ""

    return {
        "vision": [f"{p}:{m}{_mark(p)}" for p, m in _chain()],
        "text": [f"{p}:{m}{_mark(p)}" for p, m in _text_chain()],
        "last_good": st.get("last_good"),
        "last_good_text": st.get("last_good_text"),
        "usage_today": {k: v for k, v in (st.get("usage") or {}).items()
                        if k != "day"},
        "cooling": {k: int((v - now) / 60) for k, v in cd.items() if v > now},
    }
