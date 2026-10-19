"""Tovar yorlig'i / yo'riqnomasidan zavod ma'lumoti — AI o'qiydi, admin tasdiqlaydi.

Nima uchun: AI maslahatchi hozir faqat tovar NOMINI biladi, shuning uchun
"sifatlirog'i qaysi, farqi nima" degan savolga umumiy gap bilan javob
beradi. Yorliqda esa hammasi yozilgan: zavod, mamlakat, texnologiya,
tarkib, sarf, qurish vaqti. Shu ma'lumot bazaga tushsa, maslahatchi
tovarlarni haqiqiy faktlar bilan solishtiradi.

TOVAR RASMIDAN ALOHIDA. Yorliq rasmlari tovar rasmi bo'lmaydi va mijozga
hech qachon ko'rsatilmaydi: ular `DATA_DIR/labels/<pid>/` da vaqtincha
turadi va admin tasdiqlashi bilan O'CHIRILADI. Saqlanadigani — faqat
tasdiqlangan matnli kartochka.

Ikki bosqich:
  1. Har bir rasm → OCR (asl tilida so'zma-so'z, tili belgilanadi).
     Rasm modellari yiqilsa NOMDAN taxmin qilinmaydi — yorliqdagi faktni
     o'ylab topib bo'lmaydi.
  2. Barcha matn → o'zbekcha tartibli kartochka (JSON). Faqat matnda
     yozilgani; yo'q maydon bo'sh qoladi. Mamlakat va texnologiya faqat
     aniq yozilgan bo'lsa ("Made in…", "Сделано в…", "по японской технологии").

Admin kartochkani tahrirlaydi va "Tasdiqlash"ni bosadi — shundan keyingina
mijozga va AI maslahatchiga ko'rinadi.

«Do'kon bahosi» (1–5 ⭐ + izoh) — do'kon egasining o'z bahosi. Mijozga
aynan shunday nom bilan ko'rsatiladi, "xaridorlar bahosi" deb EMAS.

Endpointlar:
  GET  /api/admin/label?pid=N        — tovar + kartochka + yuklangan rasmlar
  POST /api/admin/label/read         — multipart: pid, photo (bir nechta)
  POST /api/admin/label/save         — kartochka, baho, kategoriya, tasdiq
  GET  /api/admin/labels             — kartochkasi bor tovarlar ro'yxati
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import time

from aiohttp import web

logger = logging.getLogger(__name__)
LABEL_ROUTES = web.RouteTableDef()

MAX_PHOTOS = 6

# Kartochka maydonlari — tartibi Mini App'dagi ko'rinish tartibi
FIELDS = [
    ("manufacturer", "Ishlab chiqaruvchi"),
    ("brand",        "Brend"),
    ("country",      "Ishlab chiqarilgan mamlakat"),
    ("technology",   "Texnologiya / litsenziya"),
    ("purpose",      "Nima uchun"),
    ("composition",  "Tarkibi"),
    ("usage",        "Qanday ishlatiladi"),
    ("consumption",  "Sarfi"),
    ("drying",       "Qurish / tayyor bo'lish vaqti"),
    ("volume",       "Hajmi / og'irligi"),
    ("warnings",     "Ehtiyot choralari"),
    ("storage",      "Saqlash sharoiti va muddati"),
    ("certificates", "Standart / sertifikat"),
]
FIELD_KEYS = [k for k, _ in FIELDS]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_facts (
    product_id   INTEGER PRIMARY KEY,
    facts        TEXT DEFAULT '{}',
    specs        TEXT DEFAULT '[]',
    status       TEXT DEFAULT 'draft',
    src_lang     TEXT DEFAULT '',
    ocr_text     TEXT DEFAULT '',
    category     TEXT DEFAULT '',
    rating       INTEGER DEFAULT 0,
    rating_note  TEXT DEFAULT '',
    updated_at   TEXT,
    approved_at  TEXT,
    approved_by  INTEGER DEFAULT 0
);
"""
_ready = False


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _labels_dir(pid: int) -> str:
    from database._helpers import DB_PATH
    return os.path.join(os.path.dirname(DB_PATH), "labels", str(int(pid)))


def _ensure(db) -> None:
    global _ready
    if _ready:
        return
    with db._lock:
        conn = db._conn()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
    _ready = True


def _get_row(db, pid: int):
    _ensure(db)
    with db._lock:
        conn = db._conn()
        try:
            r = conn.execute("SELECT * FROM product_facts WHERE product_id=?", (pid,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()


def _upsert(db, pid: int, **kw) -> None:
    _ensure(db)
    kw["updated_at"] = _now()
    cols = ", ".join(kw)
    qs = ", ".join("?" for _ in kw)
    upd = ", ".join(f"{k}=excluded.{k}" for k in kw)
    with db._lock:
        conn = db._conn()
        try:
            conn.execute(
                f"INSERT INTO product_facts(product_id, {cols}) VALUES(?, {qs}) "
                f"ON CONFLICT(product_id) DO UPDATE SET {upd}",
                (pid, *kw.values()))
            conn.commit()
        finally:
            conn.close()


def _all_rows(db) -> list:
    _ensure(db)
    with db._lock:
        conn = db._conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM product_facts ORDER BY updated_at DESC").fetchall()]
        finally:
            conn.close()


def _decode(row: dict) -> dict:
    try:
        facts = json.loads(row.get("facts") or "{}")
    except ValueError:
        facts = {}
    try:
        specs = json.loads(row.get("specs") or "[]")
    except ValueError:
        specs = []
    return {"facts": {k: str(facts.get(k) or "") for k in FIELD_KEYS},
            "specs": [s for s in specs if isinstance(s, dict) and s.get("k")]}


def public_info(db, pid: int) -> dict:
    """Mijozga va AI maslahatchiga: faqat TASDIQLANGAN kartochka + do'kon bahosi."""
    row = _get_row(db, pid)
    if not row:
        return {}
    out = {}
    if row.get("status") == "approved":
        d = _decode(row)
        facts = [{"k": lbl, "v": d["facts"][k]} for k, lbl in FIELDS if d["facts"].get(k)]
        if facts or d["specs"]:
            out["facts"] = facts
            out["specs"] = d["specs"]
    if int(row.get("rating") or 0) > 0:
        out["store_rating"] = int(row["rating"])
        out["store_rating_note"] = row.get("rating_note") or ""
    return out


# ── Rasmlar ────────────────────────────────────────────────────────────────

def _save_photo(pid: int, raw: bytes, n: int) -> str:
    """Yorliq mayda yozuvli — oddiy tovar rasmidan kattaroq saqlanadi (2000px)."""
    d = _labels_dir(pid)
    os.makedirs(d, exist_ok=True)
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        im.thumbnail((2000, 2000))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        raw = buf.getvalue()
    except Exception:
        pass
    path = os.path.join(d, f"{int(time.time())}_{n}.jpg")
    with open(path, "wb") as f:
        f.write(raw)
    return path


def _photos(pid: int) -> list:
    d = _labels_dir(pid)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jpg"))


def _thumb(path: str) -> str:
    """Adminga kichik ko'rinish (data URL) — alohida himoyalangan rasm yo'li
    kerak bo'lmasin; yorliq rasmlari hech qachon ochiq URL'da turmaydi."""
    try:
        from PIL import Image
        im = Image.open(path)
        im.thumbnail((360, 360))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


# ── AI ─────────────────────────────────────────────────────────────────────

_OCR_PROMPT = (
    "Bu rasmda tovar yorlig'i, qadoq yoki yo'riqnoma bor. Undagi BARCHA matnni "
    "ASL tilida, so'zma-so'z ko'chirib yoz (tarjima qilma). Birinchi qatorga "
    "'TIL: <tillar>' deb yoz (masalan: TIL: rus, ingliz). Jadval bo'lsa — "
    "'nomi: qiymati' qatorlari bilan. O'qib bo'lmaydigan joyga [?] qo'y. "
    "Matnda yo'q narsani qo'shma, izoh yozma."
)

_STRUCT_SYSTEM = (
    "Sen tovar yorlig'idagi matnni o'zbek tiliga (lotin) tarjima qilib, tartibli "
    "kartochkaga aylantirasan. QOIDALAR: 1) Faqat matnda AYNAN yozilgan narsa. "
    "Yo'q bo'lsa maydon bo'sh qatorlik qoladi — taxmin qilma, umumiy bilimdan "
    "to'ldirma. 2) 'country' — faqat 'Made in', 'Сделано в', 'Произведено', "
    "'Ishlab chiqarilgan', '原产国' kabi aniq yozuv bo'lsa. 3) 'technology' — faqat "
    "'по технологии', 'licensed', 'texnologiyasi' kabi aniq yozuv bo'lsa. "
    "4) Raqam va birliklarni o'zgartirma. 5) Har maydon 1–3 qisqa gap. "
    "6) Faqat JSON qaytar, boshqa hech narsa yozma."
)


def _struct_prompt(ocr: str, name: str, cats: list) -> str:
    keys = ", ".join(f'"{k}"' for k in FIELD_KEYS)
    return (
        f"Tovar (do'kondagi nomi): {name}\n\n"
        f"Yorliq matni (bir nechta rasmdan):\n{ocr[:9000]}\n\n"
        f"Shu ko'rinishdagi JSON qaytar:\n"
        f'{{"src_lang": "matn tillari", "fields": {{{keys} — hammasi satr}}, '
        f'"specs": [{{"k": "ko\'rsatkich nomi", "v": "qiymati"}}], '
        f'"category": "bo\'lim nomi"}}\n'
        f"'category' — mavjudlaridan birini tanla, mos kelmasa qisqa yangi nom: "
        f"{', '.join(cats[:60]) or '(hali yo`q)'}"
    )


def _parse_json(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) and isinstance(d.get("fields"), dict) else None


def _clean(d: dict) -> tuple:
    fields = d.get("fields") or {}
    facts = {k: str(fields.get(k) or "").strip()[:600] for k in FIELD_KEYS}
    specs = []
    for s in (d.get("specs") or [])[:30]:
        if isinstance(s, dict) and str(s.get("k") or "").strip():
            specs.append({"k": str(s["k"]).strip()[:80], "v": str(s.get("v") or "").strip()[:160]})
    return facts, specs, str(d.get("src_lang") or "")[:80], str(d.get("category") or "")[:60]


# ── HTTP ───────────────────────────────────────────────────────────────────

async def _admin(request):
    from database.channel_db import db as _db
    from bot.web_api import _authenticate_raw, _err
    auth = await _authenticate_raw(request, _db)
    if not auth:
        return _db, None, _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return _db, None, _err("Faqat admin uchun", 403)
    return _db, auth, None


async def _payload(db, pid: int) -> dict:
    from bot.web_api import _product_json
    p = await db.get_product_any(pid)
    row = await db._in_thread(_get_row, db, pid) or {}
    d = _decode(row) if row else {"facts": {k: "" for k in FIELD_KEYS}, "specs": []}
    photos = _photos(pid)
    loop = asyncio.get_event_loop()
    thumbs = await loop.run_in_executor(None, lambda: [_thumb(x) for x in photos])
    cats = []
    try:
        cats = [c["name"] for c in await db.get_all_categories()]
    except Exception:
        pass
    return {
        "ok": True,
        "product": _product_json(p, "admin", "dona") if p else None,
        "facts": d["facts"], "specs": d["specs"],
        "fields": [{"k": k, "label": lbl} for k, lbl in FIELDS],
        "status": row.get("status") or "", "src_lang": row.get("src_lang") or "",
        "category": row.get("category") or "", "categories": cats,
        "rating": int(row.get("rating") or 0), "rating_note": row.get("rating_note") or "",
        "photos": [t for t in thumbs if t],
        "has_ocr": bool(row.get("ocr_text")),
    }


@LABEL_ROUTES.get("/api/admin/label")
async def api_admin_label(request: web.Request):
    from bot.web_api import _err
    db, auth, err = await _admin(request)
    if err is not None:
        return err
    pid = request.query.get("pid", "")
    if not pid.isdigit():
        return _err("pid kerak")
    return web.json_response(await _payload(db, int(pid)))


@LABEL_ROUTES.post("/api/admin/label/read")
async def api_admin_label_read(request: web.Request):
    """Yorliq rasmlari → OCR → o'zbekcha kartochka (QORALAMA)."""
    from bot.web_api import _err
    from bot import ai_engine
    db, auth, err = await _admin(request)
    if err is not None:
        return err
    post = await request.post()
    pid = int(str(post.get("pid") or "0") or 0)
    p = await db.get_product_any(pid) if pid else None
    if not p:
        return _err("Tovar topilmadi", 404)

    fresh = []
    for i, field in enumerate(post.getall("photo", [])[:MAX_PHOTOS]):
        raw = field.file.read() if hasattr(field, "file") else bytes(field)
        if raw:
            fresh.append(_save_photo(pid, raw, i))
    photos = _photos(pid)
    if not photos:
        return _err("Yorliq rasmini yuklang")
    if len(photos) > MAX_PHOTOS:              # eng eskilarini tashlaymiz
        for old in photos[:-MAX_PHOTOS]:
            os.remove(old)
        photos = photos[-MAX_PHOTOS:]

    loop = asyncio.get_event_loop()
    # Yangi surat qo'shilmagan bo'lsa va matn avval o'qilgan bo'lsa — qayta
    # OCR qilmaymiz: oldingi urinish faqat tarjima bosqichida yiqilgan
    # (masalan AI kvotasi). Bu kvotani ham, vaqtni ham tejaydi.
    prev = await db._in_thread(_get_row, db, pid) or {}
    if not fresh and prev.get("ocr_text"):
        photos = []
        texts = [prev["ocr_text"]]
    else:
        texts = []
    for path in photos:
        t = await loop.run_in_executor(
            None, lambda p=path: ai_engine.read_document(
                p, _OCR_PROMPT, validate=lambda x: len(x.strip()) >= 15))
        if t:
            texts.append(t.strip())
    if not texts:
        return _err("AI rasmni o'qiy olmadi — yorug'roq va yaqinroq suratga oling "
                    "yoki birozdan keyin qayta urinib ko'ring", 503)
    ocr = "\n\n---\n\n".join(texts)
    name = (p.get("display_name") or "").strip() or p.get("name", "")
    try:
        cats = [c["name"] for c in await db.get_all_categories()]
    except Exception:
        cats = []
    raw = await loop.run_in_executor(
        None, lambda: ai_engine.chat(_struct_prompt(ocr, name, cats), system=_STRUCT_SYSTEM,
                                     validate=lambda x: _parse_json(x) is not None))
    d = _parse_json(raw)
    if not d:
        await db._in_thread(lambda: _upsert(db, pid, ocr_text=ocr[:20000], status="draft"))
        return _err("Matn o'qildi, lekin kartochkaga aylantirib bo'lmadi — qayta urinib ko'ring", 503)
    facts, specs, lang, cat = _clean(d)
    await db._in_thread(lambda: _upsert(
        db, pid, facts=json.dumps(facts, ensure_ascii=False),
        specs=json.dumps(specs, ensure_ascii=False), status="draft",
        src_lang=lang, ocr_text=ocr[:20000], category=cat))
    out = await _payload(db, pid)
    out["read_photos"] = len(texts)
    return web.json_response(out)


@LABEL_ROUTES.post("/api/admin/label/save")
async def api_admin_label_save(request: web.Request):
    from bot.web_api import _err, _read_json
    db, auth, err = await _admin(request)
    if err is not None:
        return err
    b = await _read_json(request)
    pid = int(b.get("pid") or 0)
    if not pid or not await db.get_product_any(pid):
        return _err("Tovar topilmadi", 404)
    facts = {k: str((b.get("facts") or {}).get(k) or "").strip()[:600] for k in FIELD_KEYS}
    specs = [{"k": str(s.get("k") or "").strip()[:80], "v": str(s.get("v") or "").strip()[:160]}
             for s in (b.get("specs") or [])[:30] if isinstance(s, dict) and str(s.get("k") or "").strip()]
    try:
        rating = max(0, min(int(b.get("rating") or 0), 5))
    except (TypeError, ValueError):
        rating = 0
    status = "approved" if b.get("status") == "approved" else "draft"
    if status == "approved" and not any(facts.values()) and not specs:
        return _err("Bo'sh kartochkani tasdiqlab bo'lmaydi")
    category = str(b.get("category") or "").strip()[:60]
    kw = dict(facts=json.dumps(facts, ensure_ascii=False),
              specs=json.dumps(specs, ensure_ascii=False), status=status,
              rating=rating, rating_note=str(b.get("rating_note") or "").strip()[:300],
              category=category)
    if status == "approved":
        kw.update(approved_at=_now(), approved_by=int(auth.get("tg_id") or 0))
    await db._in_thread(lambda: _upsert(db, pid, **kw))

    applied = ""
    if b.get("apply_category") and category:
        cid = 0
        for c in await db.get_all_categories():
            if c["name"].strip().lower() == category.lower():
                cid = c["id"]
                break
        if not cid:
            cid = await db.add_category(category)
        await db.set_product_category(pid, cid)
        applied = category

    removed = 0
    if status == "approved":
        # Yangi fakt paydo bo'ldi — shu bo'lim uchun AI savol taklif qilsin
        # (fonda, qoralama sifatida; admin «🧐 Maslahatlar»da ko'radi)
        from bot import api_guides
        asyncio.get_event_loop().create_task(api_guides.auto_suggest(db, pid))
        # Tasdiqlandi — yorliq rasmlari endi kerak emas (ma'lumot matn bo'lib saqlandi)
        d = _labels_dir(pid)
        removed = len(_photos(pid))
        shutil.rmtree(d, ignore_errors=True)
    try:
        from bot import api_recommend
        api_recommend._cache["at"] = 0           # kategoriya o'zgargan bo'lishi mumkin
    except Exception:
        pass
    return web.json_response({"ok": True, "status": status, "photos_removed": removed,
                              "category_applied": applied})


@LABEL_ROUTES.get("/api/admin/labels")
async def api_admin_labels(request: web.Request):
    db, auth, err = await _admin(request)
    if err is not None:
        return err
    rows = await db._in_thread(_all_rows, db)
    out = []
    for r in rows[:200]:
        p = await db.get_product_any(r["product_id"])
        if not p:
            continue
        out.append({"pid": r["product_id"],
                    "name": (p.get("display_name") or "").strip() or p.get("name", ""),
                    "status": r.get("status") or "", "rating": int(r.get("rating") or 0),
                    "updated_at": (r.get("updated_at") or "")[:16]})
    return web.json_response({"ok": True, "items": out})


def setup(app: web.Application):
    app.add_routes(LABEL_ROUTES)
