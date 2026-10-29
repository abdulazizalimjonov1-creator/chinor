"""Sennik (narx yorlig'i) chop etish — Mini App'dan buyuriladi, brauzer chop etadi.

Nima uchun shunday: senniklar ikki xil kompyuterdagi ikki xil printerda
chiqadi (ma'lumot qabul qiluvchi kompyuterda 60×40 mm rulon, server
kompyuterida 30×20 mm) va katta qog'ozda (A4) ham kerak bo'ladi. PDF
yasash uchun serverga yangi kutubxona (reportlab) kerak bo'lardi; o'rniga
server tayyor CHOP ETISH SAHIFASINI (HTML) beradi — o'lchamlar @page bilan
millimetrda belgilanadi, brauzer esa printerga aynan shu o'lchamda yuboradi.

Oqim:
  1. Mini App tovarlarni tanlaydi → POST /api/admin/pricetags
     (o'lcham, har tovarga nusxa soni). Server narx/SKU ni BAZADAN oladi —
     telefondagi eski kesh narxi senniklarga tushib qolmasin.
  2. Javobda bir martalik havola: /sennik?job=<token> (2 soat yashaydi).
  3. Havola Telegram'dan tashqi brauzerda ochiladi (Telegram ichidagi
     WebView'da Ctrl+P yo'q) va printer tanlanadi. Havolani printer ulangan
     IKKINCHI kompyuterda ochish ham mumkin — shu sababli sahifa
     autentifikatsiyasiz, faqat tasodifiy token bilan ishlaydi.

Senniklarda: do'kon logosi, tovar nomi, narxi, SKU (= tovar id), o'lchov
birligi va shtrix-kod raqami (bo'lsa).
"""

from __future__ import annotations

import html
import io
import logging
import os
import secrets
import time

from aiohttp import web

logger = logging.getLogger(__name__)
TAG_ROUTES = web.RouteTableDef()

# Bir buyurtmadagi eng ko'p sennik (nusxalari bilan) — tasodifan 10 000 ta
# yorliq printerga ketib qolmasin.
MAX_LABELS = 400
JOB_TTL = 2 * 3600          # havola 2 soat yashaydi
MAX_JOBS = 50               # xotirada shundan ortig'i saqlanmaydi

# Chop etish o'lchamlari. `page` — @page size, `one_per_page` rulon printer
# uchun (har sennik alohida varaq), `cols/rows` — A4 varaqdagi to'r.
SIZES = {
    "60x40": {"title": "60×40 mm", "page": "60mm 40mm", "w": "60mm", "h": "40mm",
              "one_per_page": True,  "mm": (60, 40), "gap_mm": 2,
              "hint": "Rulon printer (ma'lumot qabul qiluvchi kompyuter)"},
    "30x20": {"title": "30×20 mm", "page": "30mm 20mm", "w": "30mm", "h": "20mm",
              "one_per_page": True,  "mm": (30, 20), "gap_mm": 2,
              "hint": "Rulon printer (server kompyuter)"},
    "a4":    {"title": "A4 — 8 ta", "page": "A4", "w": "97mm", "h": "66mm",
              "one_per_page": False, "paper": True, "hint": "Katta qog'ozda 8 ta sennik (qirqib olinadi)"},
    "a4big": {"title": "A4 — 1 ta katta", "page": "A4", "w": "194mm", "h": "271mm",
              "one_per_page": True, "paper": True,  "hint": "Bitta katta sennik (javon / aksiya uchun)"},
}
DEFAULT_SIZE = "60x40"

# token → {"size": str, "labels": [...], "at": float}
_jobs: dict = {}
_logo_cache: bytes | None = None


# ── Logotip ────────────────────────────────────────────────────────────────

LOGO_PATH = "/sennik/logo.png"


def _logo_bytes() -> bytes:
    """Do'kon logosi — sennik sahifasi uchun kichraytirilgan PNG.

    Nega alohida manzil, data-URL emas: bitta buyurtmada 400 tagacha sennik
    bo'ladi, har biriga data-URL yozsak sahifa o'n megabaytga chiqadi. CSS
    fon rasmi ham mos emas — Chrome chop etishda fon rasmlarini SUKUT
    BO'YICHA chiqarmaydi, logo esa qolib ketmasligi kerak. Shuning uchun
    oddiy `<img>` + bir marta yuklanadigan (keshlanadigan) manzil."""
    global _logo_cache
    if _logo_cache is not None:
        return _logo_cache
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "logo.png")
    data = b""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((300, 300))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
    except Exception:
        try:                      # Pillow yo'q — faylni bor holicha beramiz
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            logger.warning(f"🏷 Sennik logosi o'qilmadi: {e}")
    _logo_cache = data
    return data


# ── Yordamchilar ───────────────────────────────────────────────────────────

def _fmt_sum(v: float) -> str:
    n = int(round(float(v or 0)))
    return f"{n:,}".replace(",", " ")


def _clean_jobs() -> None:
    now = time.time()
    for t in [t for t, j in _jobs.items() if now - j["at"] > JOB_TTL]:
        _jobs.pop(t, None)
    if len(_jobs) > MAX_JOBS:
        for t in sorted(_jobs, key=lambda x: _jobs[x]["at"])[:len(_jobs) - MAX_JOBS]:
            _jobs.pop(t, None)


# ── HTML ───────────────────────────────────────────────────────────────────

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#f1f3f5;font-family:Arial,Helvetica,"Segoe UI",sans-serif;color:#111}
.bar{position:sticky;top:0;z-index:5;background:#15803d;color:#fff;padding:10px 14px;
     display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:14px}
.bar b{font-size:15px}
.bar button{background:#fff;color:#15803d;border:0;border-radius:8px;padding:8px 16px;
     font-size:14px;font-weight:700;cursor:pointer}
.bar span.note{opacity:.9}
.sheets{padding:14px;display:flex;flex-direction:column;align-items:center;gap:10px}
.sheet{background:#fff;box-shadow:0 1px 6px rgba(0,0,0,.18)}
.grid{display:flex;flex-wrap:wrap}
.tag{position:relative;overflow:hidden;background:#fff;display:flex;flex-direction:column}

/* ── Sennik ichi ─────────────────────────────────────────────────────── */
.tag .top{display:flex;align-items:center;gap:1.5mm;border-bottom:.3mm solid #15803d;
     padding-bottom:.8mm}
.tag .top img{display:block}
.tag .shop{font-weight:700;color:#15803d;letter-spacing:.02em;line-height:1}
.tag .nm{font-weight:700;line-height:1.15;overflow:hidden;flex:1;
     display:flex;align-items:center}
.tag .pr{font-weight:700;line-height:1;white-space:nowrap;text-align:right;overflow:hidden}
.tag .pr i{font-style:normal;font-weight:400}
.tag .bot{display:flex;justify-content:space-between;align-items:flex-end;
     border-top:.3mm solid #d1d5db;padding-top:.6mm;color:#374151}
.tag .sku{font-weight:700;letter-spacing:.04em}
.tag .bc{font-family:"Courier New",monospace;letter-spacing:.02em}

@media print{
  html,body{background:#fff}
  .bar{display:none}
  .sheets{padding:0;gap:0}
  .sheet{box-shadow:none}
}
"""


def _tag_html(lb: dict, size: str) -> str:
    """Bitta sennik. Shrift o'lchamlari o'lchamga qarab (mm da, chunki
    sennikning o'zi ham mm da — brauzer masshtabidan qat'iy nazar bir xil
    chiqadi)."""
    small = size == "30x20"
    big = size == "a4big"
    name = html.escape(lb["name"])
    price = _fmt_sum(lb["price"])
    unit = html.escape(lb.get("unit") or "dona")
    sku = html.escape(str(lb.get("sku") or ""))
    bc = html.escape(lb.get("barcode") or "")

    if small:                                  # 30×20 mm — faqat eng zaruri
        s = {"pad": "1.2mm", "logo": "3.4mm", "shop": "2.3mm", "nm": "2.3mm",
             "pr": "5mm", "cur": "0.4em", "bot": "1.9mm", "gap": ".8mm"}
    elif big:                                  # A4 ga bitta katta
        s = {"pad": "14mm", "logo": "26mm", "shop": "16mm", "nm": "15mm",
             "pr": "46mm", "cur": "0.3em", "bot": "8mm", "gap": "8mm"}
    elif size == "a4":                         # A4 varaqdagi kartochka
        s = {"pad": "5mm", "logo": "11mm", "shop": "6mm", "nm": "6mm",
             "pr": "17mm", "cur": "0.35em", "bot": "4mm", "gap": "3mm"}
    else:                                      # 60×40 mm
        s = {"pad": "2.2mm", "logo": "6.5mm", "shop": "3.4mm", "nm": "3.6mm",
             "pr": "10mm", "cur": "0.33em", "bot": "2.5mm", "gap": "1.4mm"}

    img = (f'<img src="{LOGO_PATH}" alt="" style="height:{s["logo"]};width:{s["logo"]}">'
           if _logo_bytes() else "")
    cut = "" if SIZES[size]["one_per_page"] else "border:.3mm dashed #9ca3af;"
    bot_right = (f'<span class="bc" style="font-size:{s["bot"]}">{bc}</span>'
                 if bc and not small else
                 f'<span style="font-size:{s["bot"]}">{unit}</span>')
    return (
        f'<div class="tag" style="width:{SIZES[size]["w"]};height:{SIZES[size]["h"]};'
        f'padding:{s["pad"]};gap:{s["gap"]};{cut}">'
        f'  <div class="top">{img}<span class="shop" style="font-size:{s["shop"]}">CHINOR</span></div>'
        f'  <div class="nm" data-fit style="font-size:{s["nm"]}">{name}</div>'
        f'  <div class="pr" data-fit style="font-size:{s["pr"]}">{price}'
        f'<i style="font-size:{s["cur"]}"> so\'m</i></div>'
        f'  <div class="bot" style="font-size:{s["bot"]}">'
        f'<span class="sku">SKU {sku}</span>{bot_right}</div>'
        f'</div>')


def _page_html(job: dict) -> str:
    size = job["size"]
    cfg = SIZES[size]
    labels = job["labels"]

    if cfg["one_per_page"]:
        # Har sennik — alohida varaq (rulon printer uzluksiz chiqaradi)
        body = "".join(f'<div class="sheet one">{_tag_html(lb, size)}</div>' for lb in labels)
        page_css = (f'@page{{size:{cfg["page"]};margin:0}}'
                    f'.sheet.one{{page-break-after:always}}'
                    f'.sheet.one:last-child{{page-break-after:auto}}')
    else:
        # A4: 2×4 to'r — to'lmagan joy bo'sh qoladi (yorliq qog'ozi ham mos)
        per = 8
        pages = [labels[i:i + per] for i in range(0, len(labels), per)] or [[]]
        body = "".join('<div class="sheet a4"><div class="grid">' +
                       "".join(_tag_html(lb, size) for lb in chunk) +
                       "</div></div>" for chunk in pages)
        page_css = ('@page{size:A4;margin:8mm}'
                    '.sheet.a4{width:194mm;min-height:271mm;page-break-after:always}'
                    '.sheet.a4:last-child{page-break-after:auto}')

    return f"""<!DOCTYPE html>
<html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sennik — {cfg['title']} ({len(labels)} ta)</title>
<style>{_CSS}{page_css}</style></head><body>
<div class="bar">
  <b>🏷 {cfg['title']}</b>
  <span class="note">{len(labels)} ta sennik · {html.escape(cfg['hint'])}</span>
  <button onclick="window.print()">🖨 Chop etish</button>
  <span class="note">Chop etish oynasida: qog'oz o'lchami <b>{cfg['title']}</b>,
    chegara (margin) — «yo'q», masshtab — <b>100%</b>, kolontitul
    (headers&nbsp;and&nbsp;footers) — o'chirilgan bo'lsin.</span>
</div>
<div class="sheets">{body}</div>
<script>
// Uzun nom sennikdan chiqib ketmasin: joyiga sig'maguncha shriftni
// kichraytiramiz (bo'sh joy ko'p bo'lsa — kattalashtirmaymiz, chunki
// bir varaqdagi senniklar bir xil ko'rinishi kerak).
(function () {{
  function over(el) {{
    return el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
  }}
  function fit(el) {{
    var px = parseFloat(getComputedStyle(el).fontSize);
    for (var i = 0; i < 18 && over(el); i++) {{
      px *= 0.92;
      el.style.fontSize = px + 'px';
    }}
  }}
  function run() {{ document.querySelectorAll('[data-fit]').forEach(fit); }}
  run();
  // Logotip yuklanishi qatorlarni surib yuborishi mumkin — qayta o'lchaymiz
  window.addEventListener('load', function () {{
    run();
    setTimeout(function () {{ try {{ window.print(); }} catch (e) {{}} }}, 350);
  }});
}})();
</script>
</body></html>"""


# ── HTTP ───────────────────────────────────────────────────────────────────

@TAG_ROUTES.get("/api/admin/pricetag/sizes")
async def api_sizes(request: web.Request):
    """Mini App o'lchamlar ro'yxatini shu yerdan oladi (qotirib yozmasin)."""
    from database.channel_db import db as _db
    from bot.web_api import _need_admin
    auth, err = await _need_admin(request, _db, "products_view")
    if err is not None:
        return err
    from bot import print_agent
    return web.json_response({
        "ok": True, "default": DEFAULT_SIZE, "max": MAX_LABELS,
        "sizes": [{"k": k, "title": v["title"], "hint": v["hint"],
                   "paper": bool(v.get("paper"))} for k, v in SIZES.items()],
        # Printer ulangan kompyuterlar (agent dasturi o'rnatilganlari)
        "agents": print_agent.agents_list()})


@TAG_ROUTES.post("/api/admin/pricetags")
async def api_make_job(request: web.Request):
    """Tanlangan tovarlardan chop etish sahifasi yasaydi va havolasini beradi."""
    from database.channel_db import db as _db
    from bot.web_api import _need_admin, _err, _read_json, _price_pair
    auth, err = await _need_admin(request, _db, "products_view")
    if err is not None:
        return err
    b = await _read_json(request)
    size = str(b.get("size") or DEFAULT_SIZE)
    if size not in SIZES:
        return _err("O'lcham noto'g'ri")
    ctype = "optom" if b.get("optom") else "dona"

    raw = b.get("items") or []
    if not isinstance(raw, list) or not raw:
        return _err("Tovar tanlanmagan")

    labels, missing, total = [], [], 0
    for it in raw[:200]:
        if not isinstance(it, dict):
            continue
        try:
            pid = int(it.get("id") or 0)
            copies = max(1, min(int(it.get("copies") or 1), 100))
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        p = await _db.get_product_any(pid)
        if not p:
            missing.append(pid)
            continue
        # Narx BAZADAN olinadi: telefonda ochiq turgan eski ro'yxatdagi narx
        # senniklarga tushib qolmasin.
        _, summ = _price_pair(p, ctype)
        lb = {
            "name": (p.get("display_name") or "").strip() or p.get("name", ""),
            "price": summ,
            "sku": p["id"],                     # SKU = tovar id (5 xonali)
            "unit": p.get("unit") or "dona",
            "barcode": (p.get("barcode") or "").strip(),
        }
        total += copies
        if total > MAX_LABELS:
            return _err(f"Juda ko'p sennik ({total}). Bir martada {MAX_LABELS} tadan oshmasin")
        labels += [lb] * copies
    if not labels:
        return _err("Tovarlar topilmadi", 404)

    # Printer ulangan kompyuterga TO'G'RIDAN-TO'G'RI yuborish. Brauzer
    # oynasi ochilmaydi — kompyuterda ishlayotgan odamga xalaqit bermaydi.
    target = str(b.get("target") or "").strip()
    if target and target != "browser":
        from bot import print_agent
        if SIZES[size].get("paper"):
            return _err("Bu o'lcham oddiy qog'oz uchun — yorliq printeriga yubora olmaymiz")
        agent = {a["key"]: a for a in print_agent.agents_list()}.get(target)
        if not agent:
            return _err("Bunday kompyuter ro'yxatda yo'q", 404)
        if not agent["online"]:
            return _err(f"«{agent['name']}» hozir ulanmagan — kompyuter yoqilganini tekshiring", 409)
        try:
            n = await _db._in_thread(lambda: print_agent.enqueue(target, labels, size))
        except ValueError as e:
            return _err(str(e), 409)
        logger.info(f"🏷 Sennik: {n} ta ({size}) → «{agent['name']}» "
                    f"— admin {auth.get('tg_id')}")
        return web.json_response({"ok": True, "count": n, "sent_to": agent["name"],
                                  "missing": missing})

    _clean_jobs()
    token = secrets.token_hex(16)
    _jobs[token] = {"size": size, "labels": labels, "at": time.time()}
    logger.info(f"🏷 Sennik: {len(labels)} ta ({size}) — admin {auth.get('tg_id')}")
    return web.json_response({"ok": True, "token": token, "count": len(labels),
                              "url": f"/sennik?job={token}", "missing": missing,
                              "ttl_min": JOB_TTL // 60})


@TAG_ROUTES.get("/sennik")
async def page_sennik(request: web.Request):
    """Chop etish sahifasi. AUTENTIFIKATSIYASIZ — printer ulangan boshqa
    kompyuterda (brauzerda Telegram sessiyasi yo'q) ochilishi kerak.
    Himoya: tasodifiy 32-belgili token va 2 soatlik muddat."""
    token = request.query.get("job", "")
    _clean_jobs()
    job = _jobs.get(token) if token else None
    if not job:
        return web.Response(
            status=404, content_type="text/html", charset="utf-8",
            text="<!DOCTYPE html><meta charset=utf-8><body style='font-family:Arial;"
                 "padding:24px;text-align:center'><h2>🏷 Havola eskirgan</h2>"
                 "<p>Sennik havolasi 2 soat ishlaydi. Mini App'dan «Chop etish»ni "
                 "qaytadan bosing.</p></body>")
    return web.Response(text=_page_html(job), content_type="text/html", charset="utf-8")


@TAG_ROUTES.get(LOGO_PATH)
async def page_logo(request: web.Request):
    """Sennikdagi do'kon logosi. Chop etish sahifasi bilan bir xil shartda —
    autentifikatsiyasiz (logo maxfiy ma'lumot emas, kanalda ham turadi)."""
    data = _logo_bytes()
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


def setup(app: web.Application):
    app.add_routes(TAG_ROUTES)
