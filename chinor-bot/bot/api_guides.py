"""«Ustalar maslahati» kartochkalari va bo'yoq kalkulyatori.

MASLAHATLAR. Mijoz do'konda sotuvchidan so'raydigan savollar ("Qaysi emal
yaxshi?", "Pol uchun nima olay?") Mini App'da kartochka bo'lib turadi:
bosilsa javob, bizdagi tegishli tovarlar (narxi, 1 kg narxi) va "Savatga"
tugmasi chiqadi. Savolga tez javob topa olmagan xaridor ketib qoladi.

ROSTGOYLIK. Javob matnini AI QORALAMA qiladi, lekin u mijozga faqat admin
tasdiqlagach (status='published') chiqadi. AI'ga "ustalar ko'proq X ni
tanlaydi" kabi da'volarni o'ylab topmaslik, ishonchsiz joylarni
[TEKSHIRING] deb belgilash buyuriladi. Narx va qoldiq matnga yozilmaydi —
ular har ochilganda katalogdan jonli olinadi.

KALKULYATOR. Maydon × qatlam × sarf (kg/m²) + 10% zaxira → kerakli kg.
Keyin katalogdagi bir turdagi tovarlar (nomidagi "2,5kg", "700gr" dan
hajmi olinadi) oilalarga bo'linadi va har oila uchun eng arzon banka
kombinatsiyasi topiladi: 3 kg kerak bo'lsa 2 ta 2,5kg emas, 2,5kg + 0,7kg.

Endpointlar:
  GET  /api/client/guides[?pid=N]   — nashr qilingan maslahatlar (tovarga oid)
  GET  /api/client/guide?id=N       — bitta maslahat + tovarlar
  GET  /api/client/paint-calc       — ?type=emal&area=20&layers=2&q=oq
  GET  /api/admin/guides            — hammasi (qoralamalar ham)
  POST /api/admin/guide/save        — yaratish/tahrirlash/nashr
  POST /api/admin/guide/draft       — AI qoralama yozadi
  POST /api/admin/guide/delete
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import math
import re
import time

from aiohttp import web

logger = logging.getLogger(__name__)
GUIDE_ROUTES = web.RouteTableDef()

# ── Bo'yoq turlari: qaysi so'z bilan topiladi va qancha sarflanadi ─────────
# Sarf — ishlab chiqaruvchilar ko'rsatadigan odatiy oraliqning o'rtasi
# (kg / m² / bir qatlam). Sirt g'ovak bo'lsa ko'proq ketadi — mijozga
# "taxminiy" deb aytiladi va 10% zaxira qo'shiladi.
PAINT_TYPES = {
    "emal":      {"label": "Emal (metall, yog'och, eshik)", "rate": 0.14,
                  "include": ["emal"], "exclude": ["pol"]},
    "pol":       {"label": "Pol bo'yog'i", "rate": 0.13,
                  "include": ["pol"], "exclude": []},
    "lak":       {"label": "Lak", "rate": 0.08,
                  "include": ["lak"], "exclude": []},
    "emulsiya":  {"label": "Emulsiya (devor, fasad)", "rate": 0.20,
                  "include": ["emulsiya", "vodoemulsiya"], "exclude": []},
    "gruntovka": {"label": "Gruntovka", "rate": 0.15,
                  "include": ["gruntovka", "gruntofka", "grunt"], "exclude": []},
}
RESERVE = 1.10

# Birinchi ishga tushishda qo'yiladigan SAVOLLAR. Javobi bo'sh — admin
# "AI qoralama" bosib, tekshirib nashr qiladi.
SEED = [
    ("Qaysi emalni tanlash kerak — Uzcolor, Hayat yoki Weber?", "emal", "emal"),
    ("Pol uchun qaysi bo'yoq yaxshi?", "pol", "pol"),
    ("Yaxta laki va PF-283 lakning farqi nima?", "lak", "lak"),
    ("Bo'yashdan oldin gruntovka shartmi?", "gruntovka", "gruntovka"),
    ("Tashqi devor uchun qaysi emulsiya?", "emulsiya", "emulsiya"),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guides (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    answer      TEXT DEFAULT '',
    query       TEXT DEFAULT '',
    calc        TEXT DEFAULT '',
    status      TEXT DEFAULT 'draft',
    views       INTEGER DEFAULT 0,
    sort        INTEGER DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT
);
"""
_ready = False


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ensure(db) -> None:
    global _ready
    if _ready:
        return
    with db._lock:
        conn = db._conn()
        try:
            conn.executescript(_SCHEMA)
            # Avto-savollar uchun ustunlar (eski bazalarga qo'shiladi)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(guides)").fetchall()}
            if "source" not in cols:
                conn.execute("ALTER TABLE guides ADD COLUMN source TEXT DEFAULT ''")
            if "group_key" not in cols:
                conn.execute("ALTER TABLE guides ADD COLUMN group_key TEXT DEFAULT ''")
            n = conn.execute("SELECT COUNT(*) FROM guides").fetchone()[0]
            if n == 0:
                for i, (q, query, calc) in enumerate(SEED):
                    conn.execute(
                        "INSERT INTO guides(question, query, calc, status, sort, created_at, updated_at)"
                        " VALUES(?,?,?,?,?,?,?)", (q, query, calc, "draft", i, _now(), _now()))
            conn.commit()
        finally:
            conn.close()
    _ready = True


def _rows(db, where: str = "", args: tuple = ()) -> list:
    _ensure(db)
    with db._lock:
        conn = db._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM guides {where} ORDER BY sort, id", args).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def _exec(db, sql: str, args: tuple = ()) -> int:
    _ensure(db)
    with db._lock:
        conn = db._conn()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


# ── Tovarni moslash va hajmini olish ──────────────────────────────────────

# "2,5kg", "700gr", "1l", "2.5kgli" (o'zbekcha qo'shimcha bilan)
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|кг|gr|гр|g|l|л)(?:lik|li)?(?![a-zа-я])", re.I)


def size_kg(name: str) -> float:
    """Nomdagi hajm → kg. "700kg" kabi xatolar (bardoviy 700kg) gramm deb
    olinadi: 50 kg dan katta banka bo'yoq do'konida bo'lmaydi."""
    m = _SIZE_RE.search(name or "")
    if not m:
        return 0.0
    num = m.group(1).replace(",", ".")
    # "09kg", "07kg" — vergul tushib qolgan: 0,9 kg / 0,7 kg (9 kg emas)
    if re.fullmatch(r"0\d+", num):
        num = "0." + num[1:]
    v = float(num)
    unit = m.group(2).lower()
    if unit in ("gr", "гр", "g"):
        v /= 1000.0
    elif v > 50:
        v /= 1000.0
    return round(v, 3)


def _family(name: str) -> str:
    """Bir xil tovarning turli hajmlari: nomdan hajmni olib tashlaymiz."""
    base = _SIZE_RE.sub(" ", (name or "").lower())
    return re.sub(r"\s+", " ", base).strip()


def _hit(tokens: set, word: str) -> bool:
    # Qisqa so'z faqat aniq mos kelsin: "pol" → "polotensa"ni (sochiq)
    # bo'yoq deb olmasin. Uzun so'z boshi bilan ham mos keladi
    # ("emulsiya" → "vodoemulsiya" emas, lekin "gruntovka" → "gruntovkasi").
    return any(t == word or (len(word) >= 5 and t.startswith(word)) for t in tokens)


def _match(tokens: set, include: list, exclude: list) -> bool:
    if any(_hit(tokens, x) for x in exclude):
        return False
    return any(_hit(tokens, x) for x in include)


def _query_words(query: str) -> list:
    return [w.strip().lower() for w in (query or "").split(",") if w.strip()]


async def _products_for(db, query: str = "", ptype: str = "") -> list:
    from bot import api_recommend
    idx = await api_recommend._index(db)
    if ptype in PAINT_TYPES:
        inc, exc = PAINT_TYPES[ptype]["include"], PAINT_TYPES[ptype]["exclude"]
    else:
        inc, exc = _query_words(query), []
    if not inc:
        return []
    return [p for pid, p in idx.items.items() if _match(idx.toks[pid], inc, exc)]


def _client_json(p: dict, auth: dict) -> dict:
    from bot.web_api import _product_json
    u = auth.get("user") or {}
    ctype = (u.get("client_type") or "dona").lower() if auth["role"] == "client" else "dona"
    j = _product_json(p, "client", ctype)
    kg = size_kg(p.get("name", ""))
    j["size_kg"] = kg
    j["per_kg"] = round(j["price_sum"] / kg) if kg > 0 and j["price_sum"] > 0 else 0
    return j


# ── Kalkulyator ────────────────────────────────────────────────────────────

def best_combo(sizes: list, need: float):
    """sizes: [(kg, narx, qoldiq, pid)] → eng arzon (narx, [(pid, soni)], jami_kg).
    Kichik bruteforce: bir oilada odatda 1–3 xil hajm bo'ladi."""
    sizes = [s for s in sizes if s[0] > 0 and s[1] > 0 and s[2] > 0][:4]
    if not sizes:
        return None
    best = None
    ranges = []
    for kg, _, stock, _ in sizes:
        hi = min(int(stock), int(math.ceil(need / kg)) + 1)
        ranges.append(range(0, hi + 1))
    for counts in itertools.product(*ranges):
        tot_kg = sum(c * s[0] for c, s in zip(counts, sizes))
        if tot_kg + 1e-9 < need:
            continue
        price = sum(c * s[1] for c, s in zip(counts, sizes))
        key = (price, tot_kg)
        if best is None or key < best[0]:
            best = (key, [(s[3], c) for c, s in zip(counts, sizes) if c])
    if best is None:
        return None
    (price, tot_kg), lines = best
    return price, lines, tot_kg


# ── Mijoz endpointlari ─────────────────────────────────────────────────────

async def _auth(request):
    from database.channel_db import db as _db
    from bot.web_api import _authenticate
    return _db, await _authenticate(request, _db)


@GUIDE_ROUTES.get("/api/client/guides")
async def api_client_guides(request: web.Request):
    from bot.web_api import _err
    db, auth = await _auth(request)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    rows = await db._in_thread(_rows, db, "WHERE status='published'")
    pid = request.query.get("pid", "")
    calc_type = ""
    if pid.isdigit():
        # Faqat shu tovarga oid maslahatlar va kalkulyator turi
        # (tovar oynasida ko'rsatish uchun)
        from bot import api_recommend
        idx = await api_recommend._index(db)
        toks = idx.toks.get(int(pid), set())
        rows = [r for r in rows if _match(toks, _query_words(r["query"]) or [r["calc"]], [])]
        for k, t in PAINT_TYPES.items():
            if _match(toks, t["include"], t["exclude"]):
                calc_type = k
                break
    return web.json_response({
        "ok": True,
        "guides": [{"id": r["id"], "question": r["question"], "calc": r["calc"]} for r in rows],
        "calc_type": calc_type,
        "paint_types": {k: v["label"] for k, v in PAINT_TYPES.items()},
    })


@GUIDE_ROUTES.get("/api/client/guide")
async def api_client_guide(request: web.Request):
    from bot.web_api import _err
    db, auth = await _auth(request)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    gid = request.query.get("id", "")
    if not gid.isdigit():
        return _err("id kerak")
    rows = await db._in_thread(_rows, db, "WHERE id=?", (int(gid),))
    if not rows:
        return _err("Topilmadi", 404)
    g = rows[0]
    # Admin qoralamani ko'rib chiqa olsin; mijozga faqat nashr qilingani
    if g["status"] != "published" and auth["role"] != "admin":
        return _err("Topilmadi", 404)
    if auth["role"] == "client":
        await db._in_thread(_exec, db, "UPDATE guides SET views=views+1 WHERE id=?", (g["id"],))
    prods = await _products_for(db, g["query"], g["calc"] if not g["query"] else "")
    items = [_client_json(p, auth) for p in prods]
    # Tartib: avval 1 kg narxi ma'lumlar (arzonidan), keyin qolganlari
    items.sort(key=lambda j: (j["per_kg"] == 0, j["per_kg"] or j["price_sum"]))
    return web.json_response({
        "ok": True, "id": g["id"], "question": g["question"], "answer": g["answer"],
        "calc": g["calc"], "status": g["status"], "items": items[:40],
    })


@GUIDE_ROUTES.get("/api/client/paint-calc")
async def api_paint_calc(request: web.Request):
    from bot.web_api import _err
    db, auth = await _auth(request)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    ptype = request.query.get("type", "emal")
    if ptype not in PAINT_TYPES:
        return _err("Noma'lum tur")
    try:
        area = max(0.0, min(float(request.query.get("area", "0").replace(",", ".")), 5000))
        layers = max(1, min(int(request.query.get("layers", "2")), 4))
    except ValueError:
        return _err("Maydonni raqam bilan yozing")
    if area <= 0:
        return _err("Maydonni kiriting")
    need = round(area * layers * PAINT_TYPES[ptype]["rate"] * RESERVE, 2)
    color = (request.query.get("q") or "").strip().lower()

    prods = await _products_for(db, ptype=ptype)
    if color:
        prods = [p for p in prods if color in ((p.get("display_name") or p.get("name") or "").lower())]
    fams = {}
    for p in prods:
        kg = size_kg(p.get("name", ""))
        if kg <= 0:
            continue
        fams.setdefault(_family(p.get("name", "")), []).append(p)

    options = []
    for fam, plist in fams.items():
        js = {p["id"]: _client_json(p, auth) for p in plist}
        sizes = [(js[p["id"]]["size_kg"], js[p["id"]]["price_sum"], float(p.get("qty") or 0), p["id"])
                 for p in plist]
        sizes.sort(key=lambda s: -s[0])
        res = best_combo(sizes, need)
        if not res:
            continue
        price, lines, tot_kg = res
        first = js[lines[0][0]]
        options.append({
            "title": first["name"] if len(lines) == 1 else _family(first["name"]).capitalize(),
            "image_url": first.get("image_url", ""),
            "total": price, "total_kg": round(tot_kg, 2),
            "lines": [{"product_id": pid, "qty": c, "name": js[pid]["name"],
                       "size_kg": js[pid]["size_kg"], "price_sum": js[pid]["price_sum"],
                       "unit": js[pid]["unit"], "stock": js[pid]["qty"]}
                      for pid, c in lines],
        })
    options.sort(key=lambda o: (o["total"], -o["total_kg"]))
    return web.json_response({
        "ok": True, "type": ptype, "label": PAINT_TYPES[ptype]["label"],
        "need_kg": need, "rate": PAINT_TYPES[ptype]["rate"], "area": area,
        "layers": layers, "options": options[:15],
    })


# ── Admin ──────────────────────────────────────────────────────────────────

async def _admin(request):
    from bot.web_api import _err
    db, auth = await _auth(request)
    if not auth:
        return db, None, _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return db, None, _err("Faqat admin uchun", 403)
    return db, auth, None


@GUIDE_ROUTES.get("/api/admin/guides")
async def api_admin_guides(request: web.Request):
    db, auth, err = await _admin(request)
    if err is not None:
        return err
    rows = await db._in_thread(_rows, db)
    return web.json_response({"ok": True, "guides": rows,
                              "paint_types": {k: v["label"] for k, v in PAINT_TYPES.items()}})


@GUIDE_ROUTES.post("/api/admin/guide/save")
async def api_admin_guide_save(request: web.Request):
    from bot.web_api import _err, _read_json
    db, auth, err = await _admin(request)
    if err is not None:
        return err
    b = await _read_json(request)
    q = str(b.get("question") or "").strip()[:200]
    if not q:
        return _err("Savolni yozing")
    answer = str(b.get("answer") or "").strip()[:6000]
    status = "published" if b.get("status") == "published" else "draft"
    if status == "published" and not answer:
        return _err("Javobsiz nashr qilib bo'lmaydi")
    if status == "published" and "[TEKSHIRING]" in answer:
        return _err("Javobda [TEKSHIRING] belgisi qolgan — avval o'sha joylarni tekshirib, belgini o'chiring")
    query = str(b.get("query") or "").strip()[:200]
    calc = b.get("calc") if b.get("calc") in PAINT_TYPES else ""
    gid = int(b.get("id") or 0)
    if gid:
        await db._in_thread(_exec, db,
            "UPDATE guides SET question=?, answer=?, query=?, calc=?, status=?, updated_at=? WHERE id=?",
            (q, answer, query, calc, status, _now(), gid))
    else:
        gid = await db._in_thread(_exec, db,
            "INSERT INTO guides(question, answer, query, calc, status, sort, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)", (q, answer, query, calc, status, 99, _now(), _now()))
    return web.json_response({"ok": True, "id": gid, "status": status})


@GUIDE_ROUTES.post("/api/admin/guide/delete")
async def api_admin_guide_delete(request: web.Request):
    from bot.web_api import _read_json
    db, auth, err = await _admin(request)
    if err is not None:
        return err
    b = await _read_json(request)
    await db._in_thread(_exec, db, "DELETE FROM guides WHERE id=?", (int(b.get("id") or 0),))
    return web.json_response({"ok": True})


_DRAFT_SYSTEM = (
    "Sen Toshkentdagi xo'jalik-qurilish do'konining tajribali sotuvchisisan. "
    "Xaridorning savoliga o'zbek tilida (lotin), sodda va amaliy javob yozasan. "
    "QOIDALAR: "
    "1) Salomlashma, 'Yaxshi savol' dema — darhol javobdan boshla. "
    "2) Faqat ishonchli umumiy texnik bilim yoz (qaysi sirtga, qancha quriydi, "
    "ichki/tashqi, nimaga e'tibor berish). "
    "3) Brend SIFATI haqidagi har qanday baho ('yuqori sifat', 'premium', "
    "'chidamliroq', 'ustalar ko'proq tanlaydi', 'eng ko'p sotiladi') — oxiriga "
    "[TEKSHIRING] qo'y; do'kon egasi o'zi tasdiqlaydi. "
    "4) 'Arzonroq/qimmatroq' deyish mumkin faqat berilgan 1 kg narxiga qarab; "
    "lekin RAQAM yozma — narxlar sahifada avtomatik ko'rinadi. "
    "5) Formatlash: 1–2 qisqa xatbosh, keyin '• ' bilan boshlangan bir darajali "
    "ro'yxat (ichma-ich ro'yxat yo'q), muhim so'z **qalin**. Jami 90–170 so'z. "
    "Oxirida bitta qisqa amaliy maslahat. Sarlavha yozma."
)


@GUIDE_ROUTES.post("/api/admin/guide/draft")
async def api_admin_guide_draft(request: web.Request):
    from bot.web_api import _err, _read_json
    from bot import ai_engine
    db, auth, err = await _admin(request)
    if err is not None:
        return err
    b = await _read_json(request)
    gid = int(b.get("id") or 0)
    rows = await db._in_thread(_rows, db, "WHERE id=?", (gid,)) if gid else []
    g = rows[0] if rows else {"question": str(b.get("question") or ""),
                             "query": str(b.get("query") or ""), "calc": str(b.get("calc") or "")}
    if not g["question"].strip():
        return _err("Savolni yozing")
    prods = await _products_for(db, g.get("query", ""), g.get("calc", "") if not g.get("query") else "")
    lines = []
    for p in sorted(prods, key=lambda p: (p.get("display_name") or p.get("name") or ""))[:40]:
        nm = (p.get("display_name") or p.get("name") or "").strip()
        kg = size_kg(p.get("name", ""))
        price = float(p.get("sell_price") or 0)
        per = f" — 1 kg ≈ {price / kg:,.0f} so'm" if kg > 0 and price > 0 else ""
        lines.append(f"- {nm}{per}")
    prompt = (f"Xaridor savoli: {g['question']}\n\n"
              f"Do'konimizda hozir bor tovarlar (1 kg narxi faqat solishtirish uchun, "
              f"javobga raqam yozma):\n" + "\n".join(lines) +
              "\n\nShu tovarlarni hisobga olib, savolga javob yoz. Qaysi holatda "
              "qaysi turini olish kerakligini tushuntir.")

    def _ok(t: str) -> bool:
        return len(t.split()) >= 40

    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(
        None, lambda: ai_engine.web_search(prompt, system=_DRAFT_SYSTEM, validate=_ok))
    if not text:
        text = await loop.run_in_executor(
            None, lambda: ai_engine.chat(prompt, system=_DRAFT_SYSTEM, validate=_ok))
    if not text:
        return _err("AI hozir javob bermadi — keyinroq urinib ko'ring yoki o'zingiz yozing", 503)
    text = _tidy(text)
    if gid:
        await db._in_thread(_exec, db,
            "UPDATE guides SET answer=?, status='draft', updated_at=? WHERE id=?",
            (text, _now(), gid))
    return web.json_response({"ok": True, "answer": text})


# ── AI savol takliflari (avtomatik) ───────────────────────────────────────
# Tasdiqlangan yorliq kartochkasi yoki tavsifi bor tovarlardan AI xaridor
# beradigan savollarni o'zi tuzadi. Hammasi QORALAMA (source='auto') —
# admin o'qib nashr qilmaguncha mijozga chiqmaydi. Bitta guruh (bo'lim)
# uchun sutkada ko'pi bilan bir marta — bepul AI kvotasini tejash uchun.

_SUGGEST_SYSTEM = (
    "Sen xo'jalik do'koni uchun mijozlarga foydali savol-javob kartochkalari "
    "tuzasan. O'zbek tilida (lotin). QOIDALAR: 1) Savol — xaridor tanlashda "
    "haqiqatan beradigan, qiziqarli va aniq savol (masalan 'Sezgir tish uchun "
    "qaysi pasta?', 'Dezodorant va antiperspirantning farqi nima?'). "
    "2) Javob FAQAT berilgan zavod ma'lumoti va tavsiflardan; umumiy ma'lum "
    "texnik bilim qo'shsang ham bo'ladi, lekin brend sifati, 'eng yaxshi', "
    "'ko'p sotiladi' kabi baholar oxiriga [TEKSHIRING] qo'y. 3) Narx raqamini "
    "yozma. 4) Javob 70–150 so'z, '• ' bilan ro'yxat mumkin, **qalin** mumkin. "
    "5) 'keywords' — tovar NOMLARIDA uchraydigan 1–3 so'z (vergul bilan), "
    "shular orqali tegishli tovarlar topiladi. 6) Mavjud savollarni takrorlama. "
    "7) Faqat JSON."
)


def _suggest_parse(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        import json as _j
        d = _j.loads(m.group(0))
    except ValueError:
        return None
    items = d.get("items") if isinstance(d, dict) else None
    if not isinstance(items, list):
        return None
    out = []
    for it in items[:2]:
        if isinstance(it, dict) and str(it.get("question") or "").strip() and \
                len(str(it.get("answer") or "").split()) >= 30:
            out.append(it)
    return out or None


async def _groups_for(db, pid: int = 0) -> list:
    """[(group_key, nom, [tovarlar], [manba tovarlar])]. Guruh — bo'lim,
    bo'lim bo'lmasa nomning birinchi so'zi."""
    from bot import api_recommend as R, api_labels as L
    idx = await R._index(db)
    rows = await db._in_thread(L._all_rows, db)
    approved = {r["product_id"] for r in rows if r.get("status") == "approved"}

    def gkey(p):
        c = int(p.get("category_id") or 0)
        if c:
            return f"cat:{c}"
        t = R._tokens(p.get("display_name") or p.get("name") or "")
        return f"tok:{t[0]}" if t else ""

    sources = [p for q, p in idx.items.items()
               if q in approved or (p.get("description") or "").strip()]
    if pid:
        p = idx.items.get(pid) or await db.get_product_any(pid)
        sources = [p] if p else []
    groups = {}
    for p in sources:
        k = gkey(p)
        if k:
            groups.setdefault(k, {"src": [], "all": []})["src"].append(p)
    for k, g in groups.items():
        g["all"] = [p for p in idx.items.values() if gkey(p) == k][:15]
        for p in g["src"]:
            if p not in g["all"]:
                g["all"].append(p)
    out = []
    for k, g in groups.items():
        name = k.split(":", 1)[1]
        if k.startswith("cat:"):
            try:
                c = await db.get_category(int(name))
                name = (c or {}).get("name") or name
            except Exception:
                pass
        out.append((k, name, g["all"], g["src"], approved))
    return out


async def suggest_for_group(db, key, gname, members, sources, approved) -> int:
    from bot import ai_engine, api_labels as L
    existing = await db._in_thread(_rows, db)
    if any(r.get("group_key") == key and r.get("source") == "auto"
           and (r.get("created_at") or "") >= time.strftime("%Y-%m-%d %H:%M:%S",
                                                           time.localtime(time.time() - 86400))
           for r in existing):
        return -1                                  # bugun shu guruhga taklif qilingan — AI chaqirilmadi
    lines = []
    for p in members[:15]:
        nm = (p.get("display_name") or "").strip() or p.get("name", "")
        info = await db._in_thread(L.public_info, db, p["id"]) if p["id"] in approved else {}
        facts = "; ".join(f"{f['k']}: {f['v'][:120]}" for f in (info.get("facts") or []))
        desc = (p.get("description") or "").strip()[:300]
        extra = " | ".join(x for x in [facts and f"zavod: {facts}", desc and f"tavsif: {desc}",
                                       info.get("store_rating") and f"do'kon bahosi {info['store_rating']}/5"] if x)
        lines.append(f"- {nm}" + (f" | {extra}" if extra else ""))
    prompt = (f"Bo'lim: {gname}\n\nTOVARLAR:\n" + "\n".join(lines) +
              "\n\nMAVJUD SAVOLLAR (takrorlama):\n" +
              "\n".join(f"- {r['question']}" for r in existing[:40]) +
              '\n\nJSON qaytar: {"items": [{"question": "...", "answer": "...", '
              '"keywords": "so\'z1, so\'z2"}]} — 1 yoki 2 ta savol.')
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        None, lambda: ai_engine.chat(prompt, _SUGGEST_SYSTEM, 40,
                                     validate=lambda t: _suggest_parse(t) is not None))
    items = _suggest_parse(raw) or []
    from bot import api_recommend as R
    made = 0
    for it in items:
        q = str(it["question"]).strip()[:200]
        if any(_similar_q(q, r["question"]) for r in existing):
            continue
        kws = [w.strip().lower() for w in str(it.get("keywords") or "").split(",") if w.strip()]
        # Kalit so'z tovar nomlarida haqiqatan uchrashi shart — aks holda
        # maslahat ostida tovar ro'yxati bo'sh chiqadi
        name_toks = set()
        for p in members:
            name_toks |= set(R._tokens(p.get("display_name") or p.get("name") or ""))
        kws = [w for w in kws if any(t == w or (len(w) >= 5 and t.startswith(w)) for t in name_toks)]
        if not kws:
            first = R._tokens(sources[0].get("display_name") or sources[0].get("name") or "")
            kws = first[:1]
        await db._in_thread(_exec, db,
            "INSERT INTO guides(question, answer, query, calc, status, sort, source, group_key,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (q, _tidy(str(it["answer"]))[:6000], ", ".join(kws[:3]), "", "draft", 50,
             "auto", key, _now(), _now()))
        existing.append({"question": q})
        made += 1
    return made


def _tidy(text: str) -> str:
    """AI ba'zan ro'yxatni bitta qatorga yozadi ("… • **A:** … • **B:** …").
    Mini App ro'yxatni faqat alohida qatordagi '• ' dan taniydi."""
    t = re.sub(r"\s+•\s+", "\n• ", (text or "").strip())
    return re.sub(r"([^\n])\n• ", r"\1\n\n• ", t, count=1)


def _similar_q(a: str, b: str) -> bool:
    from bot import api_recommend as R
    ta, tb = set(R._tokens(a)), set(R._tokens(b))
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.7


async def auto_suggest(db, pid: int) -> None:
    """Yorliq tasdiqlangach fonda chaqiriladi — xato bo'lsa jim (log)."""
    try:
        for g in await _groups_for(db, pid):
            await suggest_for_group(db, *g)
    except Exception:
        logger.exception("auto guide suggest")


@GUIDE_ROUTES.post("/api/admin/guides/suggest")
async def api_admin_guides_suggest(request: web.Request):
    """Admin tugmasi: tavsif/yorliq kartochkasi bor bo'limlar uchun savollar
    taklif qiladi. Bir chaqiriqda ko'pi bilan 3 ta guruh (kvota)."""
    db, auth, err = await _admin(request)
    if err is not None:
        return err
    made, tried = 0, 0
    groups = await _groups_for(db)
    # Yorliq kartochkasi bor guruhlar birinchi — ularda ma'lumot boyroq
    groups.sort(key=lambda g: -sum(1 for p in g[3] if p["id"] in g[4]))
    for g in groups:
        if tried >= 3:
            break
        n = await suggest_for_group(db, *g)
        if n < 0:
            continue                       # bugun ko'rilgan guruh — kvota sarflanmadi
        tried += 1
        made += n
    return web.json_response({"ok": True, "created": made, "groups": tried})


def setup(app: web.Application):
    app.add_routes(GUIDE_ROUTES)
