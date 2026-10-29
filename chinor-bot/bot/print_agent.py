"""Sennikni USB printerga to'g'ridan-to'g'ri chop etish (brauzer oynasisiz).

Muammo: XP-365B tarmoqda ko'rinmaydi — u kompyuterga USB bilan ulangan.
Server unga to'g'ridan-to'g'ri yoza olmaydi, brauzerdan chop etish esa har
safar oyna ochib, printer tanlashni so'raydi — kompyuterda ishlayotgan
odamga xalaqit beradi.

Yechim: printer ulangan kompyuterda jim turadigan kichik dastur («agent»,
`deploy/windows/sennik-agent/`). U serverdan navbatni so'rab turadi va
kelgan baytlarni printerga uzatadi — ekranda hech narsa ochilmaydi.

Vazifa taqsimoti: KO'RINISH VA MATN — shu yerda, serverda (Pillow bilan
rasmga chiziladi, so'ng TSPL `BITMAP` buyrug'iga o'raladi). Agent esa
shunchaki baytlarni printerga yozadi va hech narsani bilmaydi. Shuning
uchun sennik ko'rinishini o'zgartirish uchun kompyuterlarga tegish shart
emas — faqat server yangilanadi.

Nega rasm, nega TSPL matn buyruqlari emas: printerning ichki shriftida
o'zbekcha matn va logotip yo'q; rasm esa Mini App'dagi ko'rinish bilan
bir xil chiqadi.

Endpointlar:
  POST /api/print-agent/poll   — agent navbatni so'raydi (uzoq kutish)
  POST /api/print-agent/done   — chop etildi / xato
  GET  /api/admin/print-agents — Mini App uchun printerlar ro'yxati
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import secrets
import time

from aiohttp import web

logger = logging.getLogger(__name__)
AGENT_ROUTES = web.RouteTableDef()

# Printer zichligi: 203 DPI = 8 nuqta/mm
DPI_MM = 8
POLL_WAIT = 20          # agent shuncha soniya kutib turadi (bo'sh navbatda)
AGENT_OFFLINE = 70      # shuncha vaqt xabar kelmasa — «o'chiq»
MAX_QUEUE = 500         # bitta agent navbatidagi eng ko'p sennik

# key → {"name", "printer", "printers", "last_seen", "last_error"}
_agents: dict = {}
# key → [{"id", "data": bytes, "name"}]
_queues: dict = {}
# key → asyncio.Event (yangi vazifa kelganda uyg'otish uchun)
_wakeups: dict = {}
_job_seq = 0
_token_cache: str | None = None


# ── Agent kaliti (parol) ───────────────────────────────────────────────────

def agent_token() -> str:
    """Agentlar uchun umumiy maxfiy kalit.

    `.env` ga qo'l bilan yozish shart emas: birinchi marta o'zi yaratilib,
    `DATA_DIR/print_agent_token.txt` ga saqlanadi va bot qayta ishga
    tushganda ham o'zgarmaydi (aks holda har restartdan keyin barcha
    kompyuterlarni qaytadan sozlash kerak bo'lardi)."""
    global _token_cache
    if _token_cache:
        return _token_cache
    env = (os.environ.get("PRINT_AGENT_TOKEN") or "").strip()
    if env:
        _token_cache = env
        return env
    from database._helpers import DB_PATH
    path = os.path.join(os.path.dirname(DB_PATH), "print_agent_token.txt")
    try:
        with open(path) as f:
            tok = f.read().strip()
    except OSError:
        tok = ""
    if not tok:
        tok = secrets.token_urlsafe(24)
        try:
            with open(path, "w") as f:
                f.write(tok)
            os.chmod(path, 0o600)
        except OSError as e:
            logger.warning(f"🖨 Agent kaliti saqlanmadi: {e}")
    _token_cache = tok
    return tok


def _check_token(request) -> bool:
    got = (request.headers.get("X-Print-Token") or "").strip()
    return bool(got) and secrets.compare_digest(got, agent_token())


# ── Sennikni rasmga chizish ────────────────────────────────────────────────

def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "fonts", name)
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _text_w(draw, text: str, font) -> int:
    return int(draw.textbbox((0, 0), text, font=font)[2])


def _wrap(draw, text: str, font, width: int, max_lines: int) -> list:
    """Matnni eniga sig'diradi; sig'masa oxirgi qatorni «…» bilan kesadi."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if _text_w(draw, test, font) <= width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and len(" ".join(lines)) < len(text):
        last = lines[-1]
        while last and _text_w(draw, last + "…", font) > width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _fit_font(draw, text: str, width: int, start: int, bold: bool = True):
    """Bitta qatorga sig'adigan eng katta shrift (narx uchun)."""
    size = start
    while size > 8:
        f = _font(size, bold)
        if _text_w(draw, text, f) <= width:
            return f
        size -= 2
    return _font(10, bold)


def render_label(lb: dict, size_key: str):
    """Sennik rasmi (PIL '1' rejimi — oq-qora, printer aynan shuni bosadi)."""
    from PIL import Image, ImageDraw
    from bot import api_pricetag

    cfg = api_pricetag.SIZES.get(size_key) or {}
    mm_w, mm_h = cfg.get("mm", (60, 40))
    W, H = int(mm_w * DPI_MM), int(mm_h * DPI_MM)
    small = W < 300                       # 30×20 — joy tor

    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    pad = max(6, W // 26)
    x0, x1 = pad, W - pad
    y = pad

    # 1) Sarlavha: logotip + do'kon nomi + chiziq
    logo_h = int(H * (0.14 if small else 0.16))
    try:
        from bot.api_pricetag import _logo_bytes
        raw = _logo_bytes()
        if raw:
            lg = Image.open(io.BytesIO(raw)).convert("L")
            lg = lg.resize((logo_h, logo_h), Image.LANCZOS)
            # Logotipni oldindan qora/oqqa ajratamiz: termal printer kulrang
            # bosa olmaydi, kulrang joy esa nuqtalarga aylanib, mayda
            # o'lchamda logotipni to'rga o'xshatib qo'yadi.
            im.paste(lg.point(lambda v: 0 if v < 195 else 255), (x0, y))
    except Exception as e:
        logger.debug(f"🖨 Logotip chizilmadi: {e}")
    shop_f = _font(int(logo_h * 0.78), True)
    d.text((x0 + logo_h + pad // 2, y + logo_h * 0.1), "CHINOR", font=shop_f, fill=0)
    y += logo_h + (3 if small else 5)
    d.line((x0, y, x1, y), fill=0, width=2)
    y += 4 if small else 7

    # 2) Nomi — 2 (kichik sennikda) yoki 3 qator
    name_f = _font(int(H * (0.085 if small else 0.09)), True)
    lines = _wrap(d, lb["name"], name_f, x1 - x0, 2 if small else 3)
    for ln in lines:
        d.text((x0, y), ln, font=name_f, fill=0)
        y += int(name_f.size * 1.15)

    # 3) Pastki qator: SKU va shtrix-kod / birlik
    foot_f = _font(int(H * (0.065 if small else 0.062)), False)
    foot_y = H - pad - int(foot_f.size * 1.2)
    d.line((x0, foot_y - 4, x1, foot_y - 4), fill=0, width=1)
    d.text((x0, foot_y), f"SKU {lb['sku']}", font=_font(foot_f.size, True), fill=0)
    right = lb.get("barcode") if (lb.get("barcode") and not small) else (lb.get("unit") or "")
    if right:
        d.text((x1 - _text_w(d, right, foot_f), foot_y), right, font=foot_f, fill=0)

    # 4) Narx — qolgan bo'sh joyni to'ldiradi, o'ng tomonda
    price = api_pricetag._fmt_sum(lb["price"])
    cur = " so'm"
    space_h = foot_y - 8 - y
    price_f = _fit_font(d, price + cur, x1 - x0, max(12, int(space_h * 0.95)))
    cur_f = _font(max(8, int(price_f.size * 0.36)), False)
    pw = _text_w(d, price, price_f) + _text_w(d, cur, cur_f)
    px = x1 - pw
    py = y + max(0, (space_h - price_f.size) // 2)
    d.text((px, py), price, font=price_f, fill=0)
    d.text((px + _text_w(d, price, price_f),
            py + price_f.size - cur_f.size - int(cur_f.size * 0.15)),
           cur, font=cur_f, fill=0)

    # Chegaraviy qiymat bilan — `convert("1")` ning o'zi Floyd-Steinberg
    # bilan «sepib» yuboradi va mayda yozuv kulrang to'r bo'lib chiqadi.
    return im.point(lambda v: 0 if v < 128 else 255).convert("1")


def tspl_label(lb: dict, size_key: str) -> bytes:
    """Bitta sennik — XP-365B uchun tayyor TSPL buyruqlari.

    `BITMAP` ma'lumotida bit 0 = qora nuqta (TSPL shunday), PIL '1'
    rejimida esa 255 (bit 1) = oq — ya'ni to'g'ridan-to'g'ri mos keladi."""
    from bot import api_pricetag
    cfg = api_pricetag.SIZES.get(size_key) or {}
    mm_w, mm_h = cfg.get("mm", (60, 40))
    gap = cfg.get("gap_mm", 2)

    im = render_label(lb, size_key)
    w_bytes = (im.width + 7) // 8
    head = (f"SIZE {mm_w} mm,{mm_h} mm\r\n"
            f"GAP {gap} mm,0 mm\r\n"
            "DIRECTION 1\r\n"
            "REFERENCE 0,0\r\n"
            "DENSITY 10\r\n"
            "SPEED 4\r\n"
            "CLS\r\n"
            f"BITMAP 0,0,{w_bytes},{im.height},0,").encode("ascii")
    return head + im.tobytes() + b"\r\nPRINT 1,1\r\n"


# ── Navbat ─────────────────────────────────────────────────────────────────

def _wake(key: str) -> None:
    ev = _wakeups.get(key)
    if ev:
        ev.set()


def enqueue(key: str, labels: list, size_key: str) -> int:
    """Senniklarni agent navbatiga qo'yadi, qo'yilganlar sonini qaytaradi."""
    global _job_seq
    q = _queues.setdefault(key, [])
    if len(q) + len(labels) > MAX_QUEUE:
        raise ValueError(f"Navbat to'lgan ({len(q)} ta kutmoqda)")
    for lb in labels:
        _job_seq += 1
        q.append({"id": _job_seq, "data": tspl_label(lb, size_key),
                  "name": lb["name"], "at": time.time()})
    _wake(key)
    return len(labels)


def agents_list() -> list:
    now = time.time()
    out = []
    for key, a in sorted(_agents.items()):
        out.append({
            "key": key, "name": a.get("name") or key,
            "printer": a.get("printer") or "",
            "online": (now - a["last_seen"]) < AGENT_OFFLINE,
            "pending": len(_queues.get(key, [])),
            "last_error": a.get("last_error") or "",
        })
    return out


# ── HTTP: agent ────────────────────────────────────────────────────────────

@AGENT_ROUTES.post("/api/print-agent/poll")
async def api_poll(request: web.Request):
    """Agent navbatni so'raydi. Navbat bo'sh bo'lsa — javobni ushlab turamiz
    (POLL_WAIT gacha): shunda sennik deyarli bir zumda chiqadi, lekin
    tarmoqqa har soniyada so'rov yog'ilmaydi."""
    from bot.web_api import _err
    if not _check_token(request):
        return _err("Kalit noto'g'ri", 403)
    try:
        b = await request.json()
    except Exception:
        b = {}
    key = str(b.get("key") or "").strip()[:40]
    if not key:
        return _err("key kerak")
    _agents[key] = {
        "name": str(b.get("name") or key)[:60],
        "printer": str(b.get("printer") or "")[:120],
        "printers": [str(p)[:120] for p in (b.get("printers") or [])[:20]],
        "last_seen": time.time(),
        "last_error": _agents.get(key, {}).get("last_error", ""),
    }
    ev = _wakeups.setdefault(key, asyncio.Event())
    q = _queues.setdefault(key, [])
    if not q:
        ev.clear()
        try:
            await asyncio.wait_for(ev.wait(), timeout=POLL_WAIT)
        except asyncio.TimeoutError:
            pass
    jobs = q[:8]                      # bir martada ko'pi bilan 8 ta
    del q[:len(jobs)]
    return web.json_response({"ok": True, "jobs": [
        {"id": j["id"], "name": j["name"],
         "data": base64.b64encode(j["data"]).decode()} for j in jobs]})


@AGENT_ROUTES.post("/api/print-agent/done")
async def api_done(request: web.Request):
    from bot.web_api import _err
    if not _check_token(request):
        return _err("Kalit noto'g'ri", 403)
    try:
        b = await request.json()
    except Exception:
        b = {}
    key = str(b.get("key") or "")[:40]
    a = _agents.get(key)
    if a is not None:
        a["last_seen"] = time.time()
        a["last_error"] = "" if b.get("ok") else str(b.get("error") or "xato")[:200]
        if not b.get("ok"):
            logger.warning(f"🖨 Agent «{key}» chop eta olmadi: {a['last_error']}")
    return web.json_response({"ok": True})


# ── HTTP: Mini App ─────────────────────────────────────────────────────────

@AGENT_ROUTES.get("/api/admin/print-agents")
async def api_agents(request: web.Request):
    from database.channel_db import db as _db
    from bot.web_api import _need_admin
    auth, err = await _need_admin(request, _db, "products_view")
    if err is not None:
        return err
    return web.json_response({"ok": True, "agents": agents_list()})


def setup(app: web.Application):
    app.add_routes(AGENT_ROUTES)
