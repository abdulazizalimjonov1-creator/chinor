"""Yetkazib beruvchilar (suppliers) — HTTP API endpointlari.

`web_api.py` god-faylini yana kattalashtirmaslik uchun ALOHIDA modul.
Route'lar `web_api.create_app()` ichida `setup(app)` orqali ro'yxatdan o'tadi.

Yordamchilar (`_authenticate`, `_admin_guard`, `_err`, `_read_json`) va `db`
handler ICHIDA lazy import qilinadi — bu circular importdan (`web_api` →
`api_suppliers` → `web_api`) qochadi va `web_api` dagi mavjud uslubga mos
keladi (u ham `db` ni handler ichida import qiladi).

Baza CRUD'i allaqachon mavjud: `database/_catalog.py` (CatalogMixin):
  get_all_suppliers / add_supplier / update_supplier / delete_supplier /
  get_supplier. Yetkazib beruvchi o'chirilsa mahsulotlar saqlanadi
  (products.supplier_id = 0).
"""

from aiohttp import web

SUPPLIER_ROUTES = web.RouteTableDef()


def _supplier_json(s: dict) -> dict:
    return {
        "id": s["id"],
        "name": s.get("name", "") or "",
        "phone": s.get("phone", "") or "",
        "note": s.get("note", "") or "",
        "product_count": int(s.get("product_count", 0) or 0),
        "low_count": int(s.get("low_count", 0) or 0),
    }


@SUPPLIER_ROUTES.get("/api/suppliers")
async def api_suppliers_list(request: web.Request):
    """Barcha yetkazib beruvchilar — har biriga product_count / low_count.
    Faqat admin ko'ra oladi (ko'rish uchun maxsus ruxsat shart emas)."""
    from database.channel_db import db as _db
    from bot.web_api import _authenticate, _err
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    items = await _db.get_all_suppliers()
    return web.json_response({
        "ok": True,
        "suppliers": [_supplier_json(s) for s in items],
    })


@SUPPLIER_ROUTES.post("/api/supplier/save")
async def api_supplier_save(request: web.Request):
    """Yangi yetkazib beruvchi (id=0) yoki mavjudini tahrirlash.
    Ruxsat: 'suppliers'."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard, _err, _read_json
    auth, err = await _admin_guard(request, _db, "suppliers")
    if err is not None:
        return err
    body = await _read_json(request)
    sid = int(body.get("id") or 0)
    name = (body.get("name") or "").strip()
    phone = (body.get("phone") or "").strip()
    note = (body.get("note") or "").strip()
    if not name:
        return _err("Yetkazib beruvchi nomi bo'sh")

    if sid:
        if not await _db.get_supplier(sid):
            return _err("Yetkazib beruvchi topilmadi", 404)
        await _db.update_supplier(sid, name=name, phone=phone, note=note)
    else:
        sid = await _db.add_supplier(name=name, phone=phone, note=note)

    s = await _db.get_supplier(sid)
    return web.json_response({"ok": True, "id": sid,
                              "supplier": _supplier_json(s or {"id": sid})})


@SUPPLIER_ROUTES.post("/api/supplier/delete")
async def api_supplier_delete(request: web.Request):
    """Yetkazib beruvchini o'chiradi — biriktirilgan mahsulotlar saqlanadi
    (supplier_id = 0 bo'ladi). Ruxsat: 'suppliers'."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard, _err, _read_json
    auth, err = await _admin_guard(request, _db, "suppliers")
    if err is not None:
        return err
    body = await _read_json(request)
    sid = int(body.get("id") or 0)
    if not sid:
        return _err("ID noto'g'ri")
    if not await _db.get_supplier(sid):
        return _err("Yetkazib beruvchi topilmadi", 404)
    await _db.delete_supplier(sid)
    return web.json_response({"ok": True})


@SUPPLIER_ROUTES.post("/api/product/supplier")
async def api_product_set_supplier(request: web.Request):
    """Mahsulotga yetkazib beruvchi biriktiradi yoki yechadi (supplier_id=0 →
    yechish). FAQAT shu bog'lanishni o'zgartiradi — nom/narx/qoldiqqa tegmaydi
    (shuning uchun /api/product/save EMAS). Ruxsat: 'products_edit'."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard, _err, _read_json
    auth, err = await _admin_guard(request, _db, "products_edit")
    if err is not None:
        return err
    body = await _read_json(request)
    pid = int(body.get("product_id") or 0)
    sid = int(body.get("supplier_id") or 0)
    if not pid:
        return _err("Mahsulot ID noto'g'ri")
    if not await _db.get_product_any(pid):
        return _err("Mahsulot topilmadi", 404)
    if sid and not await _db.get_supplier(sid):
        return _err("Yetkazib beruvchi topilmadi", 404)
    await _db.set_product_supplier(pid, sid)
    return web.json_response({"ok": True, "product_id": pid, "supplier_id": sid})


def setup(app: web.Application):
    """web_api.create_app() shu funksiyani chaqirib route'larni ulaydi."""
    app.add_routes(SUPPLIER_ROUTES)
