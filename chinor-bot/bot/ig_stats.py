"""Instagram natijalari — qaysi rolik qancha ko'rilganini yig'adi.

NEGA KERAK. Rolik joylanadi-yu, nima bo'lgani noma'lum qolardi: qaysi
ko'rinish ishladi, qaysi biri behuda ketdi — buni faqat qo'lda,
ilovaga kirib bilish mumkin edi. Endi raqamlar har kuni yig'iladi va
saqlanadi, shuning uchun «nimani kuchaytiraylik?» degan savolga
taxmin emas, o'lchov bilan javob beriladi.

ENG MUHIM KO'RSATKICH — ULASHISH (shares). Instagram rolikni aynan
shunga qarab tarqatadi: 2026-10-23 da 32 marta ulashilgan rolik 2 ta
obunachisi bor profilda 1485 ko'rish oldi, ulashilmagani esa 68 ta.

Ishlatish:
    python -m bot.ig_stats yig      # raqamlarni yangilaydi
    python -m bot.ig_stats hisobot  # eng yaxshi/yomonlarini ko'rsatadi
"""

import json
import logging
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

INTERVAL = int(os.getenv("IG_STATS_HOURS", "6")) * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reels_stats (
    media_id   TEXT PRIMARY KEY,
    ig_user    TEXT DEFAULT '',
    kind       TEXT DEFAULT '',   -- mahsulot | taqqoslash | gapiruvchi
    caption    TEXT DEFAULT '',
    permalink  TEXT DEFAULT '',
    posted_at  TEXT DEFAULT '',
    views      INTEGER DEFAULT 0,
    reach      INTEGER DEFAULT 0,
    likes      INTEGER DEFAULT 0,
    comments   INTEGER DEFAULT 0,
    saved      INTEGER DEFAULT 0,
    shares     INTEGER DEFAULT 0,
    checked_at TEXT DEFAULT ''
);
"""


def _conn():
    from bot.reels import _db_path

    c = sqlite3.connect(_db_path(), timeout=20)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _get(path: str, token: str, **params) -> dict:
    from bot.reels import _graph

    params["access_token"] = token
    url = f"{_graph()}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except Exception:
            body = {}
        return {"error": (body.get("error") or {}).get("message", str(e))}
    except Exception as e:
        return {"error": str(e)}


def _kind(caption: str) -> str:
    """Rolik turi.

    DIQQAT 1: profilda BIZDAN OLDINGI, odam yasagan roliklar ham bor va
    ular million ko'rish olgan. Ularni bizning roliklar bilan bitta
    o'rtachaga qo'shib yuborsa, hisobot ma'nosiz bo'ladi — shuning
    uchun avval «bizniki»ni ajratamiz: bizning har bir matnda do'kon
    izohi (hashtag yoki manzil) bo'ladi.

    DIQQAT 2 (2026-10-25 da tuzatildi): matn avval 300 belgida kesilardi,
    uzunroq matnlarda esa hashtag aynan shu chegaradan keyin qolib,
    O'ZIMIZNING roliklar «eski» deb belgilanardi va eski viral rolik
    bilan bitta o'rtachaga tushardi. Endi 1000 belgi saqlanadi.
    """
    c = (caption or "").strip()
    low = c.lower()
    bizniki = ("#chinor" in low or "sergeli" in low or "t.me/" in low)
    if not bizniki:
        return "eski"                      # profilning o'z, odam yasagan posti
    if c.startswith("\U0001f333"):
        return "mahsulot"
    if "marta olishdi" in low:             # gapiradigan tovarlar matni
        return "gapiruvchi"
    if "\u279c" in c or "qaysi" in low or "tanlagan" in low:
        return "taqqoslash"
    return "boshqa"


def collect() -> dict:
    """Barcha profillardagi roliklarning raqamlarini yangilaydi."""
    from bot import reels as R

    res = {"profiles": 0, "media": 0, "xato": ""}
    accounts = R.ig_accounts()
    if not accounts:
        res["xato"] = "Instagram sozlanmagan"
        return res

    c = _conn()
    try:
        for uid, base in accounts:
            res["profiles"] += 1
            token = R.ig_token(uid, base)
            med = _get(f"{uid}/media", token, limit=50,
                       fields="id,caption,timestamp,permalink,media_product_type")
            if "error" in med:
                res["xato"] = med["error"][:120]
                continue
            for m in med.get("data", []):
                ins = _get(f"{m['id']}/insights", token,
                           metric="views,reach,likes,comments,saved,shares")
                if "error" in ins:
                    ins = _get(f"{m['id']}/insights", token,
                               metric="reach,likes,comments")
                v = {}
                for d in (ins.get("data") or []):
                    try:
                        v[d["name"]] = int(d["values"][0]["value"])
                    except Exception:
                        pass
                cap = (m.get("caption") or "")[:1000]
                c.execute(
                    """INSERT INTO reels_stats
                       (media_id, ig_user, kind, caption, permalink, posted_at,
                        views, reach, likes, comments, saved, shares, checked_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(media_id) DO UPDATE SET
                         kind=excluded.kind, caption=excluded.caption,
                         permalink=excluded.permalink,
                         views=excluded.views, reach=excluded.reach,
                         likes=excluded.likes, comments=excluded.comments,
                         saved=excluded.saved, shares=excluded.shares,
                         checked_at=excluded.checked_at""",
                    (m["id"], uid, _kind(cap), cap, m.get("permalink", ""),
                     (m.get("timestamp") or "")[:19],
                     v.get("views", 0), v.get("reach", 0), v.get("likes", 0),
                     v.get("comments", 0), v.get("saved", 0), v.get("shares", 0),
                     time.strftime("%Y-%m-%d %H:%M:%S")))
                res["media"] += 1
        c.commit()
    finally:
        c.close()
    return res


def report(limit: int = 5) -> str:
    """Telegram uchun qisqa hisobot: nima ishlayapti, nima yo'q."""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM reels_stats ORDER BY views DESC").fetchall()
        turlar = c.execute(
            """SELECT kind, COUNT(*) n, AVG(views) v, AVG(shares) sh
                 FROM reels_stats GROUP BY kind ORDER BY v DESC""").fetchall()
    finally:
        c.close()
    if not rows:
        return ("📊 Hali ma'lumot yo'q.\n\n"
                "Raqamlar har 6 soatda yig'iladi.")

    out = [f"📊 <b>Instagram natijalari</b> ({len(rows)} rolik)", ""]
    out.append("<b>Tur bo'yicha o'rtacha:</b>")
    nom = {"mahsulot": "🧾 Mahsulot", "taqqoslash": "⚖️ Taqqoslash",
           "gapiruvchi": "🗣 Gapiruvchi", "boshqa": "📦 Boshqa",
           "eski": "👤 Eski (odam yasagan)"}
    for t in turlar:
        out.append(f"  {nom.get(t['kind'], t['kind'])}: "
                   f"{round(t['v'] or 0)} ko'rish · "
                   f"{round(t['sh'] or 0)} ulashish  ({t['n']} ta)")
    out += ["", "<b>Eng yaxshi:</b>"]
    for r in rows[:limit]:
        bosh = (r["caption"] or "").splitlines()[0][:34] or "—"
        out.append(f"  🔥 {r['views']} ko'rish · {r['shares']} ulashish\n"
                   f"     {bosh}")
    past = [r for r in rows if r["views"] < 100][-limit:]
    if past:
        out += ["", "<b>Eng past:</b>"]
        for r in past:
            bosh = (r["caption"] or "").splitlines()[0][:34] or "—"
            out.append(f"  ❄️ {r['views']} ko'rish — {bosh}")
    return "\n".join(out)


async def loop():
    """Har `INTERVAL` da raqamlarni yangilab turadi."""
    import asyncio

    from bot.reels import ig_configured

    if not ig_configured():
        logger.info("📊 Instagram statistikasi: sozlanmagan")
        return
    await asyncio.sleep(600)
    while True:
        try:
            res = await asyncio.get_running_loop().run_in_executor(None, collect)
            logger.info("📊 Instagram statistikasi: %s profil, %s rolik %s",
                        res["profiles"], res["media"],
                        ("| " + res["xato"]) if res["xato"] else "")
        except Exception:
            logger.exception("📊 Instagram statistikasi")
        await asyncio.sleep(INTERVAL)


def _cli(argv=None) -> int:
    # Windows konsoli cp1251 — emoji butun hisobotni yiqitardi.
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = (argv or sys.argv[1:]) or ["yig"]
    if args[0] == "yig":
        print(collect())
    elif args[0] == "hisobot":
        import re as _re

        print(_re.sub(r"<[^>]+>", "", report(8)))
    else:
        print("Ishlatish: python -m bot.ig_stats yig|hisobot")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
