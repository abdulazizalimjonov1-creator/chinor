"""AI sotuvchi-maslahatchi — faktlarga tayanib taqqoslaydi.

Oldingi maslahatchi AI'ga faqat tovar nomi, narxi va qoldig'ini berardi —
shuning uchun "qaysi biri sifatli?" savoliga umumiy gap bilan javob
qaytardi. Endi har bir nomzod tovar bilan birga beriladi:
  • tasdiqlangan zavod ma'lumoti (yorliqdan: brend, mamlakat, texnologiya,
    tarkib, sarf) — `api_labels.public_info`;
  • «Do'kon bahosi» (do'kon egasining ⭐ va izohi);
  • 1 kg narxi (nomdagi hajmdan) — "arzonroq" degan gap shunga tayanadi;
  • oxirgi 30 kundagi HAQIQIY sotuv va qaytarish soni.

AI tuzilgan javob qaytaradi: qisqa kirish, 1–3 ta tanlov ("sifatli",
"optimal", "arzon") va ular orasidagi asosiy farq. Mini App ularni rasm,
narx va "Savatga" tugmasi bilan kartochka qilib ko'rsatadi.

ROSTGOYLIK: AI'ga faqat berilgan ma'lumotdan foydalanish buyuriladi.
Mamlakat/texnologiya faqat yorliq ma'lumotida bo'lsa aytiladi; yorliq
ma'lumoti yo'q tovar haqida "zavod ma'lumoti hali kiritilmagan" deyiladi.
Do'kon bahosi "xaridorlar bahosi" deb aytilmaydi. Sotuv soni faqat real
raqamdan. Tanlangan ID lar nomzodlar ro'yxatida bo'lmasa — tashlanadi.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from aiohttp import web

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 24
TIERS = {"sifatli": "🏆 Sifatli tanlov", "optimal": "⚖️ Narx-sifat", "arzon": "💰 Arzonroq"}

_SYSTEM = (
    "Sen Toshkentdagi Chinor xo'jalik-qurilish do'konining tajribali sotuvchi-"
    "maslahatchisisan. Mijoz bilan o'zbek tilida (lotin), hurmat bilan, qisqa va "
    "aniq gaplashasan ('Hurmatli mijoz' deb murojaat qilish mumkin). "
    "QOIDALAR: "
    "1) FAQAT berilgan nomzodlar ro'yxatidagi ma'lumotdan foydalan. "
    "2) Mamlakat, texnologiya, tarkib haqida faqat 'zavod:' qismida yozilgan "
    "bo'lsa gapir. Yozilmagan bo'lsa — taxmin qilma. "
    "3) 'do'kon bahosi' — do'kon egasining bahosi; uni 'xaridorlar bahosi' dema. "
    "4) 'Ko'p sotilyapti' faqat 'sotildi30' > 0 bo'lsa; 'qaytarish yo'q' faqat "
    "sotildi30 > 0 va qaytdi30 = 0 bo'lsa. "
    "5) Arzon/qimmat — narx va '1kg' qiymatiga qarab. "
    "6) Savolga mos tovar bo'lmasa, picks bo'sh ro'yxat bo'lsin va buni ochiq ayt. "
    "7) Faqat JSON qaytar."
)


def _short(v: str, n: int = 140) -> str:
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    return v if len(v) <= n else v[:n - 1] + "…"


def _cand_line(p: dict, info: dict, idx, price: float) -> str:
    from bot.api_guides import size_kg
    name = (p.get("display_name") or "").strip() or p.get("name", "")
    parts = [f"id={p['id']}", name, f"narx={price:,.0f} so'm"]
    kg = size_kg(p.get("name", ""))
    if kg > 0 and price > 0:
        parts.append(f"1kg≈{price / kg:,.0f}")
    parts.append(f"qoldiq={float(p.get('qty') or 0):g}")
    sold = float(idx.sales.get(p["id"], 0) or 0)
    ret = float(getattr(idx, "returns", {}).get(p["id"], 0) or 0)
    parts.append(f"sotildi30={sold:g}, qaytdi30={ret:g}")
    if info.get("store_rating"):
        parts.append(f"do'kon bahosi={info['store_rating']}/5"
                     + (f" ({_short(info.get('store_rating_note'), 100)})" if info.get("store_rating_note") else ""))
    facts = info.get("facts") or []
    keep = ("Brend", "Ishlab chiqarilgan mamlakat", "Texnologiya", "Tarkibi",
            "Nima uchun", "Sarfi", "Qurish")
    f_txt = "; ".join(f"{f['k']}: {_short(f['v'], 90)}" for f in facts
                      if f["k"].startswith(keep))
    parts.append(f"zavod: {f_txt}" if f_txt else "zavod: (yorliq ma'lumoti kiritilmagan)")
    return " | ".join(parts)


def _parse(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("answer"), str):
        return None
    return d


async def _candidates(db, question: str) -> list:
    """Savolga mos tovarlar. Katalog indeksidan so'z og'irligi (IDF) bo'yicha:
    savoldagi so'zlar to'liq, AI kengaytirgan so'zlar yarim og'irlikda.
    Oddiy matn qidiruvi "Eng arzon oq emal qaysi?" kabi to'liq gapda hech
    narsa topmaydi, "emal" bo'yicha esa birinchi 8 ta rangli emal chiqib,
    oq emallar umuman nomzodga tushmay qolardi."""
    from bot.gemini_analyzer import expand_query_keywords
    from bot import api_recommend as R
    idx = await R._index(db)
    try:
        keywords = await expand_query_keywords(question)
    except Exception:
        keywords = []
    weights = {}
    for t in R._tokens(question):
        weights[t] = 1.0
    for kw in keywords[:8]:
        for t in R._tokens(kw):
            weights.setdefault(t, 0.5)

    def hit(ptoks, t):
        return t in ptoks or (len(t) >= 4 and any(x.startswith(t) for x in ptoks))

    scored = []
    for pid, ptoks in idx.toks.items():
        s = sum(w * idx.idf.get(t, 1.0) for t, w in weights.items() if hit(ptoks, t))
        if s > 0:
            scored.append((s, pid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = [idx.items[pid] for _, pid in scored[:MAX_CANDIDATES]]
    if not out:
        # Shtrix-kod yoki g'ayrioddiy yozuv — oddiy qidiruv
        for p in await db.search_products(question, limit=MAX_CANDIDATES):
            if (p.get("qty") or 0) > 0:
                out.append(p)
    return out


async def consult(request: web.Request) -> web.Response:
    from database.channel_db import db as _db
    from bot.web_api import _authenticate, _err, _read_json, _product_json, _price_pair
    from bot import ai_engine, api_labels, api_recommend
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    body = await _read_json(request)
    question = str(body.get("question") or "").strip()[:500]
    if not question:
        return _err("Savol bo'sh")
    u = auth.get("user") or {}
    ctype = (u.get("client_type") or "dona").lower() if auth["role"] == "client" else "dona"

    cands = await _candidates(_db, question)
    idx = await api_recommend._index(_db)
    infos = {}
    lines = []
    for p in cands:
        info = await _db._in_thread(api_labels.public_info, _db, p["id"])
        infos[p["id"]] = info
        lines.append(_cand_line(p, info, idx, _price_pair(p, ctype)[1]))

    prompt = (
        f"MIJOZ SAVOLI: {question}\n\n"
        f"NOMZOD TOVARLAR (faqat shulardan tanla):\n" + ("\n".join(lines) or "(mos tovar topilmadi)") +
        "\n\nShu JSON'ni qaytar:\n"
        '{"answer": "2–4 gaplik javob, tovar ID raqamlarini YOZMA", '
        '"picks": [{"id": <id>, "tier": "sifatli|optimal|arzon", '
        '"why": "nega aynan shu — 1–2 gap, faqat berilgan faktlardan"}], '
        '"compare": "tanlovlar orasidagi asosiy farq 1–3 gapda (zavod/mamlakat/'
        'tarkib/narx), faktlar yetmasa bo\'sh satr"}\n'
        "Har bir tier'dan ko'pi bilan bittadan, jami 1–3 ta. Agar mijoz arzonini "
        "so'rasa 'arzon'ni birinchi qo'y; sifatlisini so'rasa 'sifatli'ni."
    )
    loop = asyncio.get_event_loop()
    try:
        raw = await asyncio.wait_for(loop.run_in_executor(
            None, lambda: ai_engine.chat(prompt, _SYSTEM, 30,
                                         validate=lambda t: _parse(t) is not None)), timeout=45)
    except asyncio.TimeoutError:
        raw = ""
    d = _parse(raw)
    if not d:
        return web.json_response({"ok": False, "error": "AI hozir javob bermadi — birozdan keyin qayta so'rang"},
                                 status=503)

    by_id = {p["id"]: p for p in cands}
    picks, used = [], set()
    for pk in (d.get("picks") or [])[:3]:
        try:
            pid = int(pk.get("id"))
        except (TypeError, ValueError):
            continue
        tier = str(pk.get("tier") or "").lower()
        if pid not in by_id or pid in used or tier not in TIERS:
            continue                         # o'ylab topilgan ID — tashlaymiz
        used.add(pid)
        j = _product_json(by_id[pid], "client", ctype)
        j.update(infos.get(pid) or {})
        j["sold"] = float(idx.sales.get(pid, 0) or 0)
        picks.append({"tier": tier, "tier_label": TIERS[tier],
                      "why": _short(pk.get("why"), 400), "product": j})
    answer = re.sub(r"\s*\(?#?id=?\d{3,}\)?", "", d.get("answer") or "").strip()
    return web.json_response({
        "ok": True, "answer": answer,
        "compare": _short(d.get("compare"), 600),
        "picks": picks,
        # eski mijozlar (bot) uchun moslik
        "products": [pk["product"] for pk in picks],
    })
