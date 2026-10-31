"""Tovar suratlari — qaysi rasm HAQIQIY, qaysi biri umumiy.

Muammo nimada edi: katalogdagi ko'p tovarga internetdan topilgan "turi
bo'yicha" rasm qo'yilgan — bitta surat o'nlab tovarda takrorlanadi.
Saytda ham, mijoz Mini App'ida ham bir xil smesitel qator-qator chiqib,
do'kon qalbaki ko'rinardi. Shuning uchun endi mijozga FAQAT o'zimiz
suratga olgan tovarlar ko'rsatiladi.

Qanday ajratamiz (fayl nomidan):
    Mini App orqali yuklangan har bir surat `prod_<tovar id>_<vaqt>.jpg`
    deb saqlanadi (fonini kesib brendlangani `..._br.jpg`). Demak fayl
    nomidagi id — rasm AYNAN SHU tovar uchun olinganining isboti. Yangi
    tovarga, hali id berilmasdan yuklangan surat `prod_new_<vaqt>.jpg`
    bo'ladi — u ham o'zimizniki.
    Qolgani (boshqa nomdagi fayl yoki tashqi havola) — "umumiy" rasm.
    Qo'shimcha qo'riqchi: bitta manzil bir nechta tovarda uchrasa, u
    qanday nomlangan bo'lishidan qat'i nazar umumiy hisoblanadi.

Nima beradi:
    • `is_own()` / `filter_own()` — sayt, mijoz katalogi va lenta shuni
      ishlatadi;
    • `/api/photos/summary` — nechta tovarda o'z surati bor/yo'q;
    • `/api/products/no-photo` — RASMSIZ TOVARLAR ro'yxati (admin shu
      ro'yxat bo'yicha surat olib boradi): avval sotiladiganlari;
    • `/api/photos/clean` — umumiy rasmlarni tovarlardan uzadi va endi
      hech kimga kerak bo'lmagan fayllarni diskdan o'chiradi;
    • `/api/photos/mode` — "mijozga faqat o'z suratlilari" kalitini
      yoqish/o'chirish (sozlama bazada, qayta deploy shart emas).
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter

from aiohttp import web

logger = logging.getLogger(__name__)
PHOTO_ROUTES = web.RouteTableDef()

# Sozlama kaliti: 1 — mijoz/sayt faqat o'z suratli tovarlarni ko'radi.
MODE_KEY = "client_photo_only"

# `prod_<id>_<vaqt>[_br].jpg` — o'zimiz yuklagan suratning nomi.
_OWN_RE = re.compile(r"^prod_(\d+|new)_\d+", re.I)


def _base(url: str) -> str:
    """Manzildan fayl nomi: '/api/img/prod_12_7.jpg?x=1' → 'prod_12_7.jpg'."""
    u = (url or "").strip().split("?")[0].rstrip("/")
    return u.rsplit("/", 1)[-1] if u else ""


def photo_owner(url: str):
    """Surat qaysi tovar uchun olingan: id (int), 'new', yoki None."""
    m = _OWN_RE.match(_base(url))
    if not m:
        return None
    who = m.group(1).lower()
    return "new" if who == "new" else int(who)


def shared_urls(products: list) -> set:
    """Bir nechta tovarda ishlatilayotgan rasm manzillari."""
    c = Counter((p.get("image_url") or "").strip() for p in products)
    return {u for u, n in c.items() if u and n > 1}


def is_own(p: dict, shared: set | None = None) -> bool:
    """Shu tovarning O'Z surati bormi (umumiy/internetdan olingan emas)?

    Fayl nomidagi id — eng ishonchli dalil: surat AYNAN shu tovar uchun
    yuklangan. Shu surat keyin boshqa tovarga ham nusxalangan bo'lsa,
    nusxa oluvchi yutqazadi, ASL egasi esa suratini saqlab qoladi.
    Egasi noma'lum (`prod_new_…`) bo'lsa — takrorlangani umumiy sanaladi."""
    url = (p.get("image_url") or "").strip()
    if not url:
        return False
    owner = photo_owner(url)
    if owner is None:
        return False                      # umumiy / internetdan olingan nom
    if owner == "new":
        return not (shared and url in shared)
    return owner == int(p.get("id") or 0)


def filter_own(products: list) -> list:
    """Ro'yxatdan faqat o'z surati borlarini qoldiradi."""
    shared = shared_urls(products)
    return [p for p in products if is_own(p, shared)]


def client_photo_only(db) -> bool:
    """Mijozga faqat suratli tovarlarni ko'rsatamizmi? (sozlama, default — ha)"""
    try:
        return (db.get_setting(MODE_KEY, "1") or "1").strip() not in ("0", "", "no")
    except Exception:
        return True


def for_client(db, products: list) -> list:
    """Mijoz/sayt ko'radigan ro'yxat — sozlama yoqiq bo'lsa suzgichdan o'tadi."""
    if not client_photo_only(db):
        return products
    return filter_own(products)


# ─── Hisobot: nechta tovar suratli ─────────────────────────────────────────

def _split(products: list) -> tuple:
    """(o'z surati bor, umumiy rasmli, umuman rasmsiz)."""
    shared = shared_urls(products)
    own, generic, none_ = [], [], []
    for p in products:
        if is_own(p, shared):
            own.append(p)
        elif (p.get("image_url") or "").strip():
            generic.append(p)
        else:
            none_.append(p)
    return own, generic, none_


@PHOTO_ROUTES.get("/api/photos/summary")
async def api_summary(request: web.Request):
    """Suratlar holati: nechta tayyor, nechta umumiy, nechta rasmsiz."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard
    auth, err = await _admin_guard(request, _db, "products_view")
    if err is not None:
        return err
    prods = await _db.get_all_products(active_only=True)
    own, generic, none_ = _split(prods)
    # Qaysi rasm necha tovarda takrorlanayotgani — eng ko'pi birinchi.
    rep = Counter((p.get("image_url") or "").strip() for p in generic)
    top = [{"image": u, "count": n} for u, n in rep.most_common(10) if u]
    return web.json_response({
        "ok": True,
        "total": len(prods),
        "own": len(own),
        "generic": len(generic),
        "missing": len(none_),
        "in_stock_missing": len([p for p in (generic + none_)
                                 if float(p.get("qty") or 0) > 0]),
        "repeats": top,
        "client_photo_only": client_photo_only(_db),
    })


# ─── Rasmsiz tovarlar ro'yxati ─────────────────────────────────────────────

@PHOTO_ROUTES.get("/api/products/no-photo")
async def api_no_photo(request: web.Request):
    """O'z surati YO'Q tovarlar — admin shu ro'yxat bo'yicha surat oladi.

    Tartib: avval qoldig'i bor va oxirgi oyda sotilganlari. Sababi oddiy —
    minglab tovarni suratga olib bo'lmaydi, lekin sotilayotgan yuztasini
    olib chiqsa sayt va mijoz ilovasi to'ladi."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard
    auth, err = await _admin_guard(request, _db, "products_view")
    if err is not None:
        return err

    q = (request.query.get("q") or "").strip().lower()
    only_stock = (request.query.get("stock") or "1") not in ("0", "no")
    try:
        page = max(0, int(request.query.get("page", "0") or 0))
    except ValueError:
        page = 0
    size = 24

    prods = await _db.get_all_products(active_only=True)
    shared = shared_urls(prods)
    rows = [p for p in prods if not is_own(p, shared)]
    if only_stock:
        rows = [p for p in rows if float(p.get("qty") or 0) > 0]
    if q:
        rows = [p for p in rows
                if q in (p.get("name") or "").lower()
                or q in (p.get("display_name") or "").lower()
                or q in (p.get("barcode") or "").lower()]

    # Sotuv reytingi (oxirgi 30 kun) — bo'lmasa tartib qoldiqqa qarab.
    sales = {}
    try:
        from bot import api_recommend
        idx = await api_recommend._index(_db)
        sales = idx.sales or {}
    except Exception as e:                       # pragma: no cover
        logger.debug("rasmsiz ro'yxat: savdo reytingi yo'q (%s)", e)

    def _key(p):
        return (-float(sales.get(p["id"], 0) or 0),
                -float(p.get("qty") or 0),
                -(p.get("id") or 0))

    rows.sort(key=_key)
    total = len(rows)
    chunk = rows[page * size:(page + 1) * size]
    items = []
    for p in chunk:
        items.append({
            "id": p["id"],
            "name": (p.get("display_name") or p.get("name") or "").strip(),
            "qty": float(p.get("qty") or 0),
            "unit": p.get("unit") or "dona",
            "price_sum": float(p.get("sell_price") or 0),
            "sold": float(sales.get(p["id"], 0) or 0),
            # Umumiy rasm — hozir turgani ko'rinib tursin, admin farqni bilsin
            "generic_image": (p.get("image_url") or "").strip(),
        })
    return web.json_response({
        "ok": True, "total": total, "page": page,
        "has_more": (page + 1) * size < total,
        "items": items,
    })


# ─── Umumiy rasmlarni tozalash ─────────────────────────────────────────────

def _clean_sync(db, upload_dir: str, delete_files: bool = True) -> dict:
    """Umumiy rasmlarni tovarlardan uzadi, bo'shab qolgan fayllarni o'chiradi.

    `upload_dir` parametr bo'lib keladi (web_api dan) — shu tufayli bu
    funksiyani sinovda server ko'tarmasdan ham tekshirish mumkin."""
    UPLOAD_DIR = os.path.realpath(upload_dir)
    with db._lock:
        conn = db._conn()
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, image_url, image_orig_url FROM products "
                "WHERE COALESCE(image_url,'') <> '' OR COALESCE(image_orig_url,'') <> ''"
            ).fetchall()]
            shared = shared_urls(rows)
            bad = [r for r in rows if (r.get("image_url") or "").strip()
                   and not is_own(r, shared)]
            # Bo'shatiladigan fayl nomlari (asl nusxasi ham shu tovarniki emas)
            freed = set()
            for r in bad:
                for key in ("image_url", "image_orig_url"):
                    u = (r.get(key) or "").strip()
                    if u and photo_owner(u) != int(r["id"]):
                        freed.add(_base(u))
                conn.execute(
                    "UPDATE products SET image_url='', image_orig_url='' WHERE id=?",
                    (r["id"],))
            conn.commit()
            # Endi hech bir tovarda qolmagan fayllarnigina o'chiramiz
            left = set()
            for r in conn.execute(
                    "SELECT image_url, image_orig_url FROM products").fetchall():
                for u in (r["image_url"], r["image_orig_url"]):
                    b = _base(u or "")
                    if b:
                        left.add(b)
        finally:
            conn.close()

    removed = 0
    if delete_files:
        for name in freed - left:
            path = os.path.realpath(os.path.join(UPLOAD_DIR, name))
            # Faqat yuklamalar papkasi ichidagi fayl (xavfsizlik)
            if not path.startswith(UPLOAD_DIR) or not os.path.isfile(path):
                continue
            try:
                os.remove(path)
                removed += 1
            except OSError as e:                 # pragma: no cover
                logger.warning("rasm o'chmadi %s: %s", name, e)
    return {"products": len(bad), "files": removed,
            "files_free": len(freed - left)}


@PHOTO_ROUTES.post("/api/photos/clean")
async def api_clean(request: web.Request):
    """Umumiy (takrorlanadigan / internetdan olingan) rasmlarni o'chiradi.

    Tovarning o'zi tegilmaydi — faqat rasm maydoni bo'shaydi va tovar
    "rasmsiz tovarlar" ro'yxatiga tushadi."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard, _read_json, _shop_cache
    auth, err = await _admin_guard(request, _db, "products_edit")
    if err is not None:
        return err
    body = await _read_json(request)
    delete_files = bool(body.get("delete_files", True))
    from bot.web_api import UPLOAD_DIR
    out = await _db._in_thread(_clean_sync, _db, UPLOAD_DIR, delete_files)
    _shop_cache["at"] = 0.0          # sayt keshi darrov yangilansin
    try:
        from bot import api_recommend
        api_recommend._cache["idx"] = None
    except Exception:
        pass
    logger.info("🧹 Umumiy rasmlar tozalandi: %s tovar, %s fayl",
                out.get("products"), out.get("files"))
    return web.json_response({"ok": True, **out})


@PHOTO_ROUTES.post("/api/photos/mode")
async def api_mode(request: web.Request):
    """«Mijozga faqat suratli tovarlar» kalitini yoqish/o'chirish."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard, _read_json, _shop_cache
    auth, err = await _admin_guard(request, _db, "products_edit")
    if err is not None:
        return err
    body = await _read_json(request)
    on = bool(body.get("on", True))
    _db.set_setting(MODE_KEY, "1" if on else "0")
    _shop_cache["at"] = 0.0
    return web.json_response({"ok": True, "client_photo_only": on})


def setup(app: web.Application):
    app.add_routes(PHOTO_ROUTES)
