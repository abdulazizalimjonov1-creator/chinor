"""Birlashgan biznes tahlili: YesPOS + Chinor kassa + qarz daftari.

Nega kerak: savdoning asosiy qismi YesPOS kassasida (`marketplace.db`
receipts), Chinor kassasi (`pos.db` sales) esa hozircha oz ishlatiladi.
Admin «Top mijozlar» va AI analitika faqat pos.db ni o'qirdi — shuning uchun
ular deyarli bo'sh edi va qarzlarni umuman ko'rmasdi. Bu modul ikkalasini
mijoz nomi bo'yicha birlashtiradi.

Hammasi faqat O'QIYDI.
"""

import re
import sqlite3
from datetime import timedelta

from bot.api_receipts import _db as _mdb, _product_costs
from database._helpers import DB_PATH, now_local


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _since(days: int) -> str:
    return (now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _segment(c: dict, top_revenue: float, days: int, today: str) -> str:
    debt, rev = c["debt_now"], max(c["revenue"], 0.0)
    grow = c["debt_taken"] - c["debt_paid"]
    if debt > 0 and grow > 0 and (debt > rev * 0.5 or c["debt_paid"] < c["debt_taken"] * 0.5):
        return "risk"          # ko'p oladi-yu, qarzi o'syapti
    if c["first_at"] and c["first_at"] >= _since(days):
        return "new"
    if c["last_at"] and c["last_at"] < _since(45) and c["checks_all"] >= 3:
        return "sleep"         # avval kelardi, endi kelmayapti
    if debt > 0 and (grow > 0 or debt > rev * 0.5):
        return "watch"         # qarzi o'smoqda yoki savdosining yarmidan katta
    if rev > 0 and rev >= top_revenue and debt <= rev * 0.2:
        return "gold"
    return "good" if rev > 0 else "idle"


SEGMENTS = {
    "gold": "🏆 Oltin", "good": "✅ Yaxshi", "risk": "⚠️ Xavfli",
    "watch": "🟡 Kuzatish", "sleep": "😴 Uxlab qolgan", "new": "🆕 Yangi",
    "idle": "— Davrda xarid yo'q",
}


_SUMS = ("revenue", "profit", "cost_known", "checks", "returns", "debt_taken",
         "debt_paid", "debt_now", "checks_all")


def client_insights(days: int = 90, since: str = "") -> list:
    """Har mijoz: savdo, foyda, cheklar, qarz va uning o'zgarishi, segment.

    Bir egaga biriktirilgan hisoblar (debt_clients.owner) bitta qatorga
    yig'iladi; `accounts` ichida har hisob alohida (nomi, ochilgan sanasi)."""
    since = since or _since(days)
    costs = _product_costs()
    by = {}

    def get(name):
        k = _norm(name)
        if k not in by:
            by[k] = {"name": name.strip(), "revenue": 0.0, "profit": 0.0,
                     "cost_known": 0.0, "checks": 0, "returns": 0.0,
                     "debt_taken": 0.0, "debt_paid": 0.0, "debt_now": 0.0,
                     "first_at": "", "last_at": "", "checks_all": 0,
                     "sources": set()}
        return by[k]

    with _mdb() as m:
        c = m.conn
        for r in c.execute(
                "SELECT client, MIN(sold_at), MAX(sold_at), "
                "SUM(kind <> 'return') FROM receipts WHERE client <> '' GROUP BY client"):
            x = get(r[0])
            x["first_at"], x["last_at"], x["checks_all"] = r[1] or "", r[2] or "", int(r[3] or 0)
        for r in c.execute(
                "SELECT client, kind, payable, paid_debt FROM receipts "
                "WHERE client <> '' AND sold_at >= ?", (since,)):
            x = get(r[0]); x["sources"].add("YesPOS")
            pay = float(r[2] or 0)
            x["revenue"] += pay
            if r[1] == "return":
                x["returns"] += -pay
            else:
                x["checks"] += 1
            x["debt_taken"] += float(r[3] or 0)
        # Foyda — tannarxi ma'lum qatorlar bo'yicha (tannarx — joriy, taxminiy)
        for r in c.execute(
                "SELECT r.client, i.product_id, SUM(i.qty), SUM(i.amount) "
                "FROM receipt_items i JOIN receipts r ON r.msg_id = i.msg_id "
                "WHERE r.client <> '' AND r.sold_at >= ? GROUP BY r.client, i.product_id",
                (since,)):
            cost = costs.get(int(r[1] or 0))
            if cost:
                x = get(r[0])
                x["profit"] += float(r[3] or 0) - float(r[2] or 0) * cost
                x["cost_known"] += float(r[3] or 0)
        for r in c.execute(
                "SELECT client, SUM(amount) FROM debt_payments "
                "WHERE client <> '' AND paid_at >= ? GROUP BY client", (since,)):
            get(r[0])["debt_paid"] += float(r[1] or 0)
        import marketplace.debt as D
        D.ensure_schema(c)
        for name, d in D.all_debts(c).items():
            get(name)["debt_now"] = float(d or 0)
        own = {_norm(k): v.strip() for k, v in D.owners(c).items()}

    # Chinor kassasi (pos.db) — oz, lekin bor
    pos = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        for r in pos.execute(
                "SELECT c.shop_name, s.total, COALESCE(s.is_return,0), s.created_at "
                "FROM sales s JOIN clients c ON c.id = s.client_id "
                "WHERE COALESCE(s.is_internal,0)=0 AND s.created_at >= ?", (since,)):
            x = get(r[0] or ""); x["sources"].add("Chinor")
            t = float(r[1] or 0)
            x["revenue"] += -t if r[2] else t
            x["checks"] += 0 if r[2] else 1
            x["last_at"] = max(x["last_at"], r[3] or "")
    finally:
        pos.close()

    rows = [x for k, x in by.items() if k and (x["revenue"] or x["debt_now"] > 0)]

    # Bir egaga biriktirilgan hisoblarni bitta mijozga yig'amiz
    groups = {}
    for x in rows:
        owner = own.get(_norm(x["name"]))
        if not owner:
            groups[("", _norm(x["name"]))] = x
            continue
        g = groups.get(("o", _norm(owner)))
        if g is None:
            g = groups[("o", _norm(owner))] = {
                "name": owner, "first_at": "", "last_at": "", "sources": set(),
                "accounts": [], **{k: 0 for k in _SUMS}}
        for k in _SUMS:
            g[k] += x[k]
        g["first_at"] = min(filter(None, (g["first_at"], x["first_at"])), default="")
        g["last_at"] = max(g["last_at"], x["last_at"])
        g["sources"] |= x["sources"]
        g["accounts"].append({"name": x["name"], "opened": (x["first_at"] or "")[:10],
                              "last_at": (x["last_at"] or "")[:10],
                              "revenue": round(x["revenue"]), "checks": x["checks"],
                              "debt_now": round(x["debt_now"]),
                              "debt_change": round(x["debt_taken"] - x["debt_paid"])})
    rows = list(groups.values())
    for g in rows:
        if g.get("accounts"):
            g["accounts"].sort(key=lambda a: a["opened"] or "9")
    revs = sorted((x["revenue"] for x in rows if x["revenue"] > 0), reverse=True)
    top20 = revs[max(0, len(revs) // 5 - 1)] if revs else 0
    today = now_local().strftime("%Y-%m-%d")
    for x in rows:
        x["segment"] = _segment(x, top20, days, today)
        x["segment_label"] = SEGMENTS[x["segment"]]
        x["avg_check"] = x["revenue"] / x["checks"] if x["checks"] else 0
        x["debt_change"] = x["debt_taken"] - x["debt_paid"]
        x["sources"] = sorted(x["sources"])
        for k in ("revenue", "profit", "returns", "debt_taken", "debt_paid",
                  "debt_now", "avg_check", "debt_change", "cost_known"):
            x[k] = round(x[k])
        x["last_at"] = (x["last_at"] or "")[:16]
        x["first_at"] = (x["first_at"] or "")[:10]
    rows.sort(key=lambda x: x["revenue"], reverse=True)
    return rows


def top_of_month(limit: int = 3) -> list:
    """Joriy kalendar oyining eng ko'p savdo qilgan mijozlari (guruhlangan)."""
    start = now_local().strftime("%Y-%m-01 00:00:00")
    rows = client_insights(31, since=start)
    return [{"name": x["name"], "revenue": x["revenue"], "profit": x["profit"],
             "checks": x["checks"], "debt_now": x["debt_now"],
             "accounts": len(x.get("accounts") or []) or 1}
            for x in rows if x["revenue"] > 0][:limit]


def business_summary(days: int = 30) -> dict:
    """Davr va oldingi davr: manba bo'yicha savdo/foyda, to'lov turlari, qarz, top tovarlar."""
    now = now_local()
    cur = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    prev = (now - timedelta(days=2 * days)).strftime("%Y-%m-%d %H:%M:%S")
    costs = _product_costs()

    def yes_period(c, a, b):
        r = c.execute(
            "SELECT COALESCE(SUM(payable),0), SUM(kind<>'return'), "
            "COALESCE(SUM(CASE WHEN kind='return' THEN -payable END),0), "
            "COALESCE(SUM(paid_cash),0), COALESCE(SUM(paid_card),0), "
            "COALESCE(SUM(paid_click),0), COALESCE(SUM(paid_debt),0) "
            "FROM receipts WHERE sold_at >= ? AND sold_at < ?", (a, b)).fetchone()
        profit = known = 0.0
        for pid, q, amt in c.execute(
                "SELECT i.product_id, SUM(i.qty), SUM(i.amount) FROM receipt_items i "
                "JOIN receipts r ON r.msg_id=i.msg_id WHERE r.sold_at >= ? AND r.sold_at < ? "
                "GROUP BY i.product_id", (a, b)):
            cost = costs.get(int(pid or 0))
            if cost:
                profit += float(amt or 0) - float(q or 0) * cost
                known += float(amt or 0)
        rev = float(r[0] or 0)
        return {"revenue": round(rev), "checks": int(r[1] or 0), "returns": round(r[2] or 0),
                "cash": round(r[3] or 0), "card": round(r[4] or 0), "click": round(r[5] or 0),
                "debt": round(r[6] or 0), "profit": round(profit),
                "profit_coverage_pct": round(100 * known / rev) if rev > 0 else 0}

    end = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    with _mdb() as m:
        c = m.conn
        yes_cur, yes_prev = yes_period(c, cur, end), yes_period(c, prev, cur)
        top = []
        for name, q, amt, pid in c.execute(
                "SELECT i.name, SUM(i.qty), SUM(i.amount), i.product_id FROM receipt_items i "
                "JOIN receipts r ON r.msg_id=i.msg_id WHERE r.sold_at >= ? "
                "GROUP BY i.product_id ORDER BY SUM(i.amount) DESC LIMIT 15", (cur,)):
            cost = costs.get(int(pid or 0)) or 0
            top.append({"name": name, "qty": round(q or 0, 2), "revenue": round(amt or 0),
                        "profit": round((amt or 0) - (q or 0) * cost) if cost else None})
        paid = c.execute("SELECT COALESCE(SUM(amount),0) FROM debt_payments WHERE paid_at >= ?",
                         (cur,)).fetchone()[0]
        import marketplace.debt as D
        D.ensure_schema(c)
        debts = [d for d in D.all_debts(c).values() if d > 0]

    pos = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        def chi(a, b):
            r = pos.execute(
                "SELECT COALESCE(SUM(CASE WHEN is_return=1 THEN -total ELSE total END),0), "
                "SUM(COALESCE(is_return,0)=0) FROM sales "
                "WHERE COALESCE(is_internal,0)=0 AND created_at >= ? AND created_at < ?",
                (a, b)).fetchone()
            return {"revenue": round(r[0] or 0), "checks": int(r[1] or 0)}
        chi_cur, chi_prev = chi(cur, end), chi(prev, cur)
        stock = pos.execute(
            "SELECT COUNT(*), SUM(qty<0), SUM(is_active=1 AND COALESCE(sell_price,0)<=0), "
            "COALESCE(SUM(CASE WHEN qty>0 THEN qty*cost_price END),0) FROM products").fetchone()
    finally:
        pos.close()

    return {
        "days": days,
        "yespos": {"current": yes_cur, "previous": yes_prev},
        "chinor": {"current": chi_cur, "previous": chi_prev},
        "total_revenue": yes_cur["revenue"] + chi_cur["revenue"],
        "total_revenue_prev": yes_prev["revenue"] + chi_prev["revenue"],
        "debt_total": round(sum(debts)), "debtors": len(debts),
        "debt_paid_period": round(paid),
        "top_products": top,
        "products": int(stock[0] or 0), "negative_stock": int(stock[1] or 0),
        "zero_price": int(stock[2] or 0), "stock_value_cost": round(stock[3] or 0),
    }


def _alias(name: str, aliases) -> str:
    if aliases is None:
        return name
    for k, v in aliases.items():
        if v == name:
            return k
    k = f"Mijoz#{len(aliases) + 1:02d}"
    aliases[k] = name
    return k


def alias_question(q: str, aliases: dict) -> str:
    """Admin savolidagi haqiqiy ismlarni taxallusga almashtiradi (uzunidan boshlab)."""
    for k, v in sorted(aliases.items(), key=lambda kv: -len(kv[1])):
        if v and len(v) >= 3:
            q = re.sub(re.escape(v), k, q, flags=re.I)
    return q


def unalias(text: str, aliases: dict) -> str:
    """AI javobidagi taxalluslarni haqiqiy ismlarga qaytaradi (#10 dan oldin #1 bo'lmasin)."""
    for k in sorted(aliases, key=len, reverse=True):
        text = text.replace(k, aliases[k])
    return text


def _m(v) -> str:
    return f"{round(v or 0):,}".replace(",", " ")


def ai_context_text(days: int = 30, client_days: int = 90, aliases: dict = None) -> str:
    """AI uchun matn: umumiy raqamlar + mijozlar segmentlari (YesPOS + Chinor).

    `aliases` (dict) berilsa — mijoz ismlari tashqi AI'ga «Mijoz#07» bo'lib
    ketadi va lug'atga {taxallus: haqiqiy ism} yoziladi; javobni ko'rsatishdan
    oldin `unalias()` bilan qaytariladi. Tashqi AI xizmatlari (ayniqsa bepul
    modellar) so'rovni saqlashi mumkin — mijoz ismi va qarzi ularda qolmasin."""
    s = business_summary(days)
    y, yp, ch = s["yespos"]["current"], s["yespos"]["previous"], s["chinor"]["current"]
    L = [f"=== UMUMIY (oxirgi {days} kun, oldingi {days} kun bilan) — YesPOS + Chinor kassa ===",
         f"Jami savdo: {_m(s['total_revenue'])} so'm (oldingi davr {_m(s['total_revenue_prev'])})",
         f"YesPOS: savdo {_m(y['revenue'])}, cheklar {y['checks']}, qaytarish {_m(y['returns'])}, "
         f"taxminiy foyda {_m(y['profit'])} (tannarx ma'lum bo'lgan savdo {y['profit_coverage_pct']}%)",
         f"YesPOS oldingi davr: savdo {_m(yp['revenue'])}, foyda {_m(yp['profit'])}, cheklar {yp['checks']}",
         f"To'lov turlari: naqd {_m(y['cash'])}, karta {_m(y['card'])}, click {_m(y['click'])}, "
         f"qarzga {_m(y['debt'])}",
         f"Chinor kassa: savdo {_m(ch['revenue'])}, cheklar {ch['checks']}",
         f"Qarz: jami {_m(s['debt_total'])} so'm, {s['debtors']} qarzdor; davrda to'langan {_m(s['debt_paid_period'])}",
         f"Ombor: {s['products']} tovar, tannarx bo'yicha qiymati {_m(s['stock_value_cost'])}; "
         f"manfiy qoldiq {s['negative_stock']}, narxsiz {s['zero_price']}",
         "", f"=== TOP TOVARLAR ({days} kun) ==="]
    for i, t in enumerate(s["top_products"], 1):
        L.append(f"{i}. {t['name']} — {t['qty']:g} dona, {_m(t['revenue'])} so'm"
                 + (f", foyda {_m(t['profit'])}" if t["profit"] is not None else ""))
    rows = client_insights(client_days)
    L += ["", f"=== MIJOZLAR ({client_days} kun; qarz — hozirgi) ==="]
    for seg in ("gold", "risk", "watch", "sleep", "new", "good"):
        part = [x for x in rows if x["segment"] == seg][:12]
        if not part:
            continue
        L.append(f"-- {SEGMENTS[seg]} ({sum(1 for x in rows if x['segment'] == seg)} ta) --")
        for x in part:
            L.append(f"• {_alias(x['name'], aliases)}: savdo {_m(x['revenue'])}, foyda {_m(x['profit'])}, "
                     f"{x['checks']} chek, qarz {_m(x['debt_now'])} "
                     f"(davrda olgan {_m(x['debt_taken'])}, to'lagan {_m(x['debt_paid'])}), "
                     f"oxirgi xarid {x['last_at'][:10] or '-'}")
    return "\n".join(L)
