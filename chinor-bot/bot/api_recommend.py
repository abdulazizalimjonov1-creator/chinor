"""Mijoz Mini App'i uchun tavsiyalar — «o'xshash tovarlar» va «Siz uchun».

Ikki xil tavsiya (Baymard tadqiqoti shu ikkisini ajratadi):
  • O'XSHASH (alternativa) — mijoz ko'rayotgan tovarning boshqa turlari:
    nomidagi so'zlar, bo'lim, «qayerda ishlatiladi» teglari, narx yaqinligi.
  • BIRGA OLINADIGAN — haqiqiy cheklarda shu tovar bilan birga sotilganlar.
    Savdo tarixi bo'lmasa, bu signal shunchaki nolga teng.

«Siz uchun» lentasi mijozning qiziqishidan quriladi: Mini App oxirgi
ko'rilgan va savatga solingan tovarlar ro'yxatini yuboradi (brauzerda
saqlanadi, serverga hech narsa yozilmaydi), server shu tovarlarning
so'zlari/bo'limlari bo'yicha butun katalogni baholaydi.

ROSTGOYLIK: "Ko'p sotilayotgan" faqat oxirgi 30 kunda HAQIQATAN sotilgan
tovarlarga qo'yiladi (`recent_sales_rank`). Savdo tarixi bo'lmasa — bo'lim
umuman ko'rsatilmaydi, alifbo tartibini "top" deb ko'rsatmaymiz.

Endpointlar:
  GET /api/client/similar?pid=N&page=P          — o'xshash tovarlar lentasi
  GET /api/client/feed?seed=1,2,3&page=P        — «Siz uchun» lentasi
"""

from __future__ import annotations

import json
import os
import math
import re
import time
from collections import Counter, defaultdict

from aiohttp import web

RECO_ROUTES = web.RouteTableDef()

PAGE = 12
SALES_DAYS = 30

# Nomdagi ma'nosiz so'zlar: o'lchov birliklari, qo'shimchalar, bog'lovchilar.
# Ular hamma tovarda uchraydi va o'xshashlikni buzadi ("10 tali" ≠ o'xshash).
_STOP = {
    "tali", "talik", "dona", "sht", "pcs", "li", "lik", "ta", "sm", "mm", "kg",
    "gr", "ml", "metr", "va", "bilan", "uchun", "the", "and", "for", "new",
    "mini", "big", "set", "nabor", "для", "и", "с", "шт", "мм", "см",
}
# Ikki harfli, lekin ma'noli so'zlar (rang): "oq emal" ≠ "emal"
_SHORT_OK = {"oq"}
_TOKEN_RE = re.compile(r"[a-zа-яёʻʼ'’ўқғҳ0-9]+", re.IGNORECASE)


def _tokens(text: str) -> list:
    out = []
    for t in _TOKEN_RE.findall((text or "").lower()):
        t = t.strip("ʻʼ'’")
        if (len(t) < 3 and t not in _SHORT_OK) or t.isdigit() or t in _STOP:
            continue
        out.append(t)
    return out


# ── Katalog indeksi (60 soniya kesh) ─────────────────────────────────────────

_cache = {"at": 0.0, "idx": None}


class _Index:
    """Faqat qoldig'i bor faol tovarlar. Har tovar uchun so'z to'plami va
    IDF og'irliklari — kam uchraydigan so'z (brend, model) ko'proq og'irlik
    oladi, "set", "katta" kabi umumiy so'zlar kam."""

    def __init__(self, products: list, sales_rank: dict, co: dict):
        self.items = {}
        self.toks = {}
        df = Counter()
        for p in products:
            if (p.get("qty") or 0) <= 0 or not p.get("is_active", 1):
                continue
            pid = p["id"]
            name = (p.get("display_name") or "").strip() or p.get("name", "")
            toks = set(_tokens(name))
            # Teglar ham so'z — "oshxona", "hammom" kabi qiziqishni bog'laydi
            for tag in (p.get("usage_tags") or "").split(","):
                toks |= {"#" + t for t in _tokens(tag)}
            self.items[pid] = p
            self.toks[pid] = toks
            df.update(toks)
        n = max(len(self.items), 1)
        self.idf = {t: math.log(1 + n / c) for t, c in df.items()}
        self.by_tok = defaultdict(set)
        for pid, toks in self.toks.items():
            for t in toks:
                self.by_tok[t].add(pid)
        self.sales = sales_rank
        self.co = co

    def price(self, p) -> float:
        return float(p.get("sell_price") or 0)

    def has_img(self, p) -> bool:
        return bool(p.get("image_url"))


def _norm_name(s: str) -> str:
    s = (s or "").lower().replace("’", "'").replace("ʻ", "'").replace("ʼ", "'").replace("`", "'")
    return re.sub(r"\s+", " ", s).strip()


_FUZZY = {}          # chekdagi nom → pid (yoki 0); qayta-qayta hisoblanmasin
_LAT = str.maketrans("авекмнорстухАВЕКМНОРСТУХ", "aBekmhopctyxABEKMHOPCTYX")


def _match_name(nm: str, by_name: dict) -> int:
    """Aniq nom; bo'lmasa kirill-lotin aralash harflarni tenglab qayta;
    bo'lmasa ≥0.9 o'xshashlik (yozilishdagi mayda farq). Topilmasa 0."""
    pid = by_name.get(nm)
    if pid:
        return pid
    if nm in _FUZZY:
        return _FUZZY[nm]
    import difflib
    lat = nm.translate(_LAT).lower()
    pid = by_name.get(lat, 0)
    if not pid:
        got = difflib.get_close_matches(nm, by_name.keys(), n=1, cutoff=0.9)
        pid = by_name[got[0]] if got else 0
    _FUZZY[nm] = pid
    return pid


def _yespos_rows(db, since: str) -> list:
    """YesPOS cheklari (userbot `marketplace.tg_receipts` yig'adi →
    data/marketplace.db). Chekdagi tovar NOMI Chinor bazasidagi nom bilan
    bir xil (tovarlar YesPOS'dan sinxronlanadi) — shu bo'yicha bog'lanadi.
    Qaytaradi: [(items_json, is_return)] yoki [] (cheklar hali yo'q)."""
    import sqlite3
    from database._helpers import DB_PATH
    mp = os.path.join(os.path.dirname(DB_PATH), "marketplace.db")
    if not os.path.exists(mp):
        return []
    by_name = {}
    for p in db._all_products(active_only=False):
        for nm in (p.get("name"), p.get("display_name")):
            if nm:
                by_name.setdefault(_norm_name(nm), p["id"])
    try:
        c = sqlite3.connect(mp, timeout=10)
        rows = c.execute(
            "SELECT r.msg_id, r.kind, i.name, i.qty FROM receipts r "
            "JOIN receipt_items i ON i.msg_id = r.msg_id WHERE r.day >= ?",
            (since,)).fetchall()
        c.close()
    except Exception:
        return []
    by_msg = defaultdict(lambda: {"ret": False, "items": []})
    for msg_id, kind, name, qty in rows:
        rec = by_msg[msg_id]
        rec["ret"] = kind == "return"
        pid = _match_name(_norm_name(name), by_name)
        if pid:
            rec["items"].append({"product_id": pid, "qty": abs(float(qty or 0))})
    return [(json.dumps(r["items"]), 1 if r["ret"] else 0)
            for r in by_msg.values() if r["items"]]


def _recent_sales_sync(db, days: int = SALES_DAYS):
    """(sotuv_reytingi, birga_sotilganlar, qaytarishlar) — oxirgi `days` kun.

    Manbalar (hammasi haqiqiy tranzaksiya):
      • YesPOS cheklari — do'konning asosiy kassasi; bo'lsa ASOSIY manba;
      • Chinor kassasi savdolari (`sales`) — faqat YesPOS cheklari bo'lmasa
        (ikki tizim bir savdoni yozgan bo'lsa ikki marta sanalmasin);
      • Mini App/bot buyurtmalari — YesPOS'da yo'q, doim qo'shiladi."""
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    rank = Counter()
    returns = Counter()
    buyers = Counter()
    co = defaultdict(Counter)
    baskets = []
    rows = _yespos_rows(db, since)
    with db._lock:
        conn = db._conn()
        try:
            if not rows:
                rows = [(r["items"], r["is_return"]) for r in conn.execute(
                    "SELECT items, COALESCE(is_return, 0) AS is_return FROM sales "
                    "WHERE created_at >= ?", (since,)).fetchall()]
            rows += [(r["items"], 0) for r in conn.execute(
                "SELECT items FROM orders WHERE created_at >= ? AND status != 'cancelled'",
                (since,)).fetchall()]
        finally:
            conn.close()
    for raw, is_ret in rows:
        try:
            items = json.loads(raw) if raw else []
        except Exception:
            continue
        if is_ret:
            # Qaytarilgan chek — sotuv emas; alohida sanaladi ("qaytarish yo'q"
            # degan gap faqat shu raqam 0 bo'lsa aytiladi)
            for it in items:
                if it.get("product_id"):
                    returns[int(it["product_id"])] += abs(float(it.get("qty") or 0))
            continue
        basket = set()
        for it in items:
            pid = it.get("product_id")
            q = float(it.get("qty") or 0)
            if pid and q > 0:
                rank[int(pid)] += q
                basket.add(int(pid))
        for pid in basket:
            buyers[pid] += 1              # nechta chekda uchradi
        if 1 < len(basket) <= 30:
            baskets.append(basket)
    _LAST_BUYERS.clear()
    _LAST_BUYERS.update(buyers)
    for b in baskets:
        for a in b:
            for c in b:
                if a != c:
                    co[a][c] += 1
    return dict(rank), co, dict(returns)


_LAST_BUYERS = Counter()


def _build_index_sync(db):
    rank, co, returns = _recent_sales_sync(db)
    idx = _Index(db._all_products(active_only=True), rank, co)
    idx.returns = returns
    idx.buyers = Counter(_LAST_BUYERS)
    return idx


async def _index(db) -> _Index:
    now = time.time()
    if _cache["idx"] is None or now - _cache["at"] > 60:
        _cache["idx"] = await db._in_thread(_build_index_sync, db)
        _cache["at"] = now
    return _cache["idx"]


def recent_top(idx: _Index, limit: int = 12) -> list:
    """Oxirgi 30 kunda haqiqatan sotilgan, hozir omborda bor tovarlar.
    Tartib — nechta XARIDOR olgani (cheklar soni), miqdor emas: aks holda
    metrlab/donalab sotiladigan arzon narsa (qop, kabel) doim birinchi bo'lardi."""
    score = getattr(idx, "buyers", None) or idx.sales
    pids = [pid for pid, _ in sorted(score.items(), key=lambda x: -x[1])
            if pid in idx.items]
    return [idx.items[pid] for pid in pids[:limit]]


# ── Baholash ─────────────────────────────────────────────────────────────────

def _similar_scores(idx: _Index, pid: int) -> list:
    base = idx.items.get(pid)
    btoks = idx.toks.get(pid)
    if base is None:
        return []
    cands = set()
    for t in btoks:
        cands |= idx.by_tok.get(t, set())
    cat = int(base.get("category_id") or 0)
    if cat:
        cands |= {q for q, p in idx.items.items() if int(p.get("category_id") or 0) == cat}
    cands |= set(idx.co.get(pid, {}))
    cands.discard(pid)
    bprice = idx.price(base)
    scored = []
    for q in cands:
        p = idx.items.get(q)
        if p is None:
            continue
        common = btoks & idx.toks[q]
        s = sum(idx.idf.get(t, 0) for t in common)
        if cat and int(p.get("category_id") or 0) == cat:
            s += 3.0
        s += 2.0 * min(idx.co.get(pid, {}).get(q, 0), 3)
        if bprice > 0 and idx.price(p) > 0:
            ratio = idx.price(p) / bprice
            if 0.5 <= ratio <= 2.0:
                s += 0.8
        if idx.has_img(p):
            s += 0.6
        if s > 0.5:
            scored.append((s, q))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [q for _, q in scored]


def _feed_scores(idx: _Index, seeds: list) -> list:
    """Qiziqish profili: ko'rilgan tovarlarning so'zlari (yangisi og'irroq)."""
    prof = Counter()
    cats = Counter()
    for i, pid in enumerate(reversed(seeds[-30:])):     # eng yangisi birinchi
        w = 1.0 / (1 + i * 0.15)
        for t in idx.toks.get(pid, ()):
            prof[t] += w * idx.idf.get(t, 0)
        c = int((idx.items.get(pid) or {}).get("category_id") or 0)
        if c:
            cats[c] += w
    seen = set(seeds)
    day = int(time.time() // 86400)
    scored = []
    for q, p in idx.items.items():
        s = sum(prof.get(t, 0) for t in idx.toks[q])
        c = int(p.get("category_id") or 0)
        if c:
            s += 2.0 * cats.get(c, 0)
        s += 0.5 * math.log1p(idx.sales.get(q, 0))
        if idx.has_img(p):
            s += 1.5
        if q in seen:
            s *= 0.3                       # ko'rganini qayta-qayta tiqmaymiz
        # Kunlik barqaror aralashtirish: profil bo'sh bo'lsa ham lenta har kuni
        # bir xil emas, lekin varaqlaganda tartib sakramaydi.
        s += ((q * 2654435761 + day) % 1000) / 1000.0 * 0.4
        scored.append((s, q))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return _diversify([q for _, q in scored], idx)


def _diversify(pids: list, idx: _Index) -> list:
    """Bir xil turdagi tovar (nomining birinchi so'zi) ketma-ket 2 tadan ortiq
    turmasin — lenta "faqat shina" bo'lib qolmasin, Instagram ham aralashtiradi."""
    def kind(q):
        t = _tokens(idx.items[q].get("display_name") or idx.items[q].get("name") or "")
        return t[0] if t else ""

    out, held = [], []
    for q in pids:
        k = kind(q)
        if len(out) >= 2 and kind(out[-1]) == k == kind(out[-2]):
            held.append(q)
        else:
            out.append(q)
        # Kutib turganlardan biri endi sig'sa — qo'yamiz
        if held and not (len(out) >= 2 and kind(out[-1]) == kind(held[0]) == kind(out[-2])):
            out.append(held.pop(0))
    return out + held


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _page(request) -> int:
    try:
        return max(0, min(int(request.query.get("page", "0")), 200))
    except ValueError:
        return 0


def _ids(raw: str) -> list:
    out = []
    for x in (raw or "").split(","):
        x = x.strip()
        if x.isdigit():
            out.append(int(x))
    return out[-40:]


async def _respond(request, pids: list, idx: _Index, auth: dict, extra: dict = None):
    from bot.web_api import _product_json
    page = _page(request)
    chunk = pids[page * PAGE:(page + 1) * PAGE]
    u = auth.get("user") or {}
    ctype = (u.get("client_type") or "dona").lower() if auth["role"] == "client" else "dona"
    items = []
    for q in chunk:
        j = _product_json(idx.items[q], "client", ctype)
        j["sold"] = float(idx.sales.get(q, 0))
        items.append(j)
    body = {"ok": True, "items": items, "page": page,
            "has_more": (page + 1) * PAGE < len(pids)}
    if extra:
        body.update(extra)
    return web.json_response(body)


@RECO_ROUTES.get("/api/client/similar")
async def api_client_similar(request: web.Request):
    from database.channel_db import db as _db
    from bot.web_api import _authenticate, _err
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    try:
        pid = int(request.query.get("pid", "0"))
    except ValueError:
        return _err("pid kerak")
    idx = await _index(_db)
    sim = _similar_scores(idx, pid)
    # O'xshashlar tugagach lenta to'xtab qolmasin — davomi «Siz uchun»
    # (shu tovar urug' bo'ladi), Instagram'dagi kabi cheksiz.
    seen = set(sim) | {pid}
    tail = [q for q in _feed_scores(idx, [pid]) if q not in seen]
    return await _respond(request, sim + tail, idx, auth,
                          {"similar_count": len(sim)})


@RECO_ROUTES.get("/api/client/feed")
async def api_client_feed(request: web.Request):
    from database.channel_db import db as _db
    from bot.web_api import _authenticate, _err
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    idx = await _index(_db)
    seeds = [s for s in _ids(request.query.get("seed", "")) if s in idx.items]
    return await _respond(request, _feed_scores(idx, seeds), idx, auth,
                          {"personal": bool(seeds)})


def setup(app: web.Application):
    app.add_routes(RECO_ROUTES)
