"""Savdo tarixi — HTTP API (Mini App «🧾 Savdo tarixi» bo'limi uchun).

Manba: `marketplace.db` dagi `receipts` / `receipt_items` — YesPOS boti
Telegramga yuborgan cheklardan yig'ilgan (marketplace/tg_receipts.py).

Nega Chinor `sales` jadvali emas: haqiqiy savdo YesPOS kassasida bo'ladi,
Chinor bazasidagi `sales` da bor-yo'g'i bir nechta yozuv bor. Mini App'dagi
eski «Cheklar» ekrani o'sha jadvalni ko'rsatadi va u TEGILMAYDI — bu yangi
bo'lim yonida turadi.

Endpointlar:
  GET /api/sales/history?days=1&limit=100   — cheklar ro'yxati + kunlik jami
  GET /api/sales/receipt?id=N               — bitta chekning tovarlari
"""

import os
import sys

from aiohttp import web

SALES_ROUTES = web.RouteTableDef()

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# Chek turi → Mini App ko'rsatadigan yorliq.
_KIND = {"sale": "savdo", "sale_deferred": "nasiya", "return": "qaytarish"}


def _db():
    """`marketplace.db` ni ochadi (faqat o'qish uchun ishlatiladi)."""
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from marketplace.db import MarketplaceDB

    return MarketplaceDB()


@SALES_ROUTES.get("/api/sales/history")
async def api_sales_history(request: web.Request):
    """Oxirgi cheklar + kunlik yig'indi. Ruxsat: 'stats'."""
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err

    auth, err = await _need_admin(request, _db_pos, "stats")
    if err is not None:
        return err

    try:
        days = max(1, min(90, int(request.query.get("days") or 1)))
        limit = max(1, min(300, int(request.query.get("limit") or 100)))
    except ValueError:
        days, limit = 1, 100

    try:
        with _db() as mdb:
            c = mdb.conn
            rows = c.execute(
                """SELECT msg_id, doc_no, kind, cashier, client, sold_at, day,
                          total, discount, payable
                     FROM receipts
                    WHERE day >= date('now', ?)
                    ORDER BY sold_at DESC LIMIT ?""",
                (f"-{days - 1} day", limit),
            ).fetchall()
            # Qatorlar sonini alohida olamiz — ro'yxatda «3 tovar» deb chiqadi.
            counts = dict(c.execute(
                """SELECT i.msg_id, COUNT(*) FROM receipt_items i
                     JOIN receipts r ON r.msg_id = i.msg_id
                    WHERE r.day >= date('now', ?)
                    GROUP BY i.msg_id""",
                (f"-{days - 1} day",),
            ).fetchall())
            daily = c.execute(
                """SELECT day, COUNT(*) n, SUM(payable) summa
                     FROM receipts WHERE day >= date('now', ?)
                    GROUP BY day ORDER BY day DESC""",
                (f"-{days - 1} day",),
            ).fetchall()
    except Exception as e:
        return _err(f"Savdo tarixi o'qilmadi: {e}")

    return web.json_response({
        "ok": True,
        "days": days,
        "items": [{
            "id": r["msg_id"],
            "doc_no": r["doc_no"],
            "kind": _KIND.get(r["kind"], r["kind"]),
            "cashier": r["cashier"] or "",
            "client": (r["client"] or "").strip(),
            "at": r["sold_at"],
            "total": round(r["total"] or 0),
            "discount": round(r["discount"] or 0),
            "payable": round(r["payable"] or 0),
            "lines": int(counts.get(r["msg_id"], 0)),
        } for r in rows],
        "daily": [{"day": d["day"], "count": d["n"],
                   "sum": round(d["summa"] or 0)} for d in daily],
    })


@SALES_ROUTES.get("/api/sales/summary")
async def api_sales_summary(request: web.Request):
    """Kunlik aylanma, foyda va top tovarlar — HAQIQIY cheklardan.

    Foyda cheklarda yo'q (chekda faqat sotuv narxi bor), shuning uchun tannarx
    `pos.db` dan olinadi va tovar bo'yicha qo'shiladi. Tannarxi yo'q yoki
    katalogga bog'lanmagan qatorlar foydaga QO'SHILMAYDI — ular ulushi
    `profit_coverage` da ko'rinadi, ya'ni raqam qanchalik to'liq ekani
    yashirilmaydi.

    Ruxsat: 'stats'.
    """
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err

    auth, err = await _need_admin(request, _db_pos, "stats")
    if err is not None:
        return err

    try:
        days = max(1, min(365, int(request.query.get("days") or 30)))
    except ValueError:
        days = 30

    try:
        costs = _product_costs()
        with _db() as mdb:
            c = mdb.conn
            since = f"-{days - 1} day"

            daily = [dict(r) for r in c.execute(
                """SELECT day, COUNT(*) n, SUM(payable) summa
                     FROM receipts WHERE day >= date('now', ?)
                    GROUP BY day ORDER BY day DESC""", (since,))]

            rows = c.execute(
                """SELECT r.day, i.product_id, i.qty, i.amount
                     FROM receipt_items i JOIN receipts r ON r.msg_id = i.msg_id
                    WHERE r.day >= date('now', ?)""", (since,)).fetchall()

            top = [dict(r) for r in c.execute(
                """SELECT i.name, SUM(i.qty) dona, SUM(i.amount) summa
                     FROM receipt_items i JOIN receipts r ON r.msg_id = i.msg_id
                    WHERE r.day >= date('now', ?)
                    GROUP BY i.name ORDER BY summa DESC LIMIT 10""", (since,))]

            cashiers = [dict(r) for r in c.execute(
                """SELECT cashier, COUNT(*) n, SUM(payable) summa
                     FROM receipts WHERE day >= date('now', ?) AND cashier <> ''
                    GROUP BY cashier ORDER BY summa DESC LIMIT 6""", (since,))]
    except Exception as e:
        return _err(f"Hisob o'qilmadi: {e}")

    # Foyda: faqat tannarxi ma'lum qatorlar bo'yicha.
    profit_by_day, known, unknown = {}, 0.0, 0.0
    for r in rows:
        amount = float(r["amount"] or 0)
        cost = costs.get(int(r["product_id"] or 0))
        if cost:
            profit_by_day[r["day"]] = (profit_by_day.get(r["day"], 0.0)
                                       + amount - float(r["qty"] or 0) * cost)
            known += amount
        else:
            unknown += amount

    today = daily[0]["day"] if daily else ""
    total_sum = sum(float(d["summa"] or 0) for d in daily)
    total_cnt = sum(int(d["n"] or 0) for d in daily)
    profit_sum = sum(profit_by_day.values())

    return web.json_response({
        "ok": True,
        "days": days,
        "today": {
            "day": today,
            "count": int(daily[0]["n"]) if daily else 0,
            "revenue": round(float(daily[0]["summa"] or 0)) if daily else 0,
            "profit": round(profit_by_day.get(today, 0.0)),
        },
        "period": {
            "count": total_cnt,
            "revenue": round(total_sum),
            "profit": round(profit_sum),
            "avg_check": round(total_sum / total_cnt) if total_cnt else 0,
        },
        # Foyda qancha savdoni qamrab olgani (100% dan kam bo'lsa — tannarx
        # yoki katalog bog'lanishi yetishmaydi).
        "profit_coverage": round(100 * known / max(known + unknown, 1)),
        "daily": [{"day": d["day"], "count": int(d["n"]),
                   "sum": round(float(d["summa"] or 0)),
                   "profit": round(profit_by_day.get(d["day"], 0.0))}
                  for d in daily],
        "top_products": [{"name": t["name"], "qty": round(float(t["dona"] or 0), 1),
                          "sum": round(float(t["summa"] or 0))} for t in top],
        "cashiers": [{"name": x["cashier"], "count": int(x["n"]),
                      "sum": round(float(x["summa"] or 0))} for x in cashiers],
    })


def recent_sales(limit: int = 200, since: str = "") -> list:
    """YesPOS cheklarini `/api/sync/recent-sales` SHAKLIDA qaytaradi.

    Nega shu shaklda: admin ilovasidagi «Cheklar» ro'yxati ham, «Sotuvlar
    grafigi» ham shu endpointdan oziqlanadi. Ular Chinor `sales` jadvalini
    o'qiydi va u deyarli bo'sh — savdo YesPOS kassasida bo'ladi, shuning
    uchun grafik ham, ro'yxat ham bo'sh chiqardi.

    `source` = "YesPOS" — ro'yxatda offline savdo ekani ko'rinib tursin.
    `id` manfiy: Chinor `sales.id` bilan to'qnashmasligi uchun.
    """
    costs = _product_costs()
    where, params = "", []
    if since:
        where = "WHERE r.sold_at > ?"
        params.append(since)
    params.append(int(limit))

    with _db() as mdb:
        rows = mdb.conn.execute(
            f"""SELECT msg_id, doc_no, kind, cashier, client, sold_at,
                       total, discount, payable,
                       COALESCE(paid_cash,0) paid_cash, COALESCE(paid_card,0) paid_card,
                       COALESCE(paid_click,0) paid_click, COALESCE(paid_debt,0) paid_debt
                  FROM receipts r {where}
                 ORDER BY sold_at DESC LIMIT ?""", params).fetchall()
        ids = [r["msg_id"] for r in rows]
        items_by = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for i in mdb.conn.execute(
                f"SELECT msg_id, name, qty, amount, product_id FROM receipt_items "
                f"WHERE msg_id IN ({marks}) ORDER BY line_no", ids
            ):
                items_by.setdefault(i["msg_id"], []).append(i)

    out = []
    for r in rows:
        items, cost_sum = [], 0.0
        for i in items_by.get(r["msg_id"], []):
            qty = float(i["qty"] or 0)
            cost = costs.get(int(i["product_id"] or 0)) or 0.0
            cost_sum += cost * qty
            items.append({
                "name": i["name"],
                "qty": qty,
                "price_sum": float(i["amount"] or 0),
                "product_id": int(i["product_id"] or 0),
            })
        total = float(r["payable"] or 0)
        is_return = r["kind"] == "return"
        is_nasiya = r["kind"] == "sale_deferred"
        out.append({
            "id": -int(r["msg_id"]),
            "receipt_no": str(r["doc_no"] or ""),
            "created_at": r["sold_at"],
            "cashier_name": r["cashier"] or "",
            "source": "YesPOS",
            "total_sum": total,
            "profit_sum": round(total - cost_sum, 2),
            "subtotal_sum": float(r["total"] or 0),
            "discount_sum": float(r["discount"] or 0),
            "payment": _payment_label(r, is_return, is_nasiya),
            "paid_cash": float(r["paid_cash"] or 0),
            "paid_card": float(r["paid_card"] or 0),
            "paid_other": float(r["paid_click"] or 0),
            "debt_part": float(r["paid_debt"] or 0),
            "client_name": (r["client"] or "").strip(),
            "client_id": 0,
            "is_internal": 0,
            "is_nasiya": 1 if is_nasiya else 0,
            "is_return": 1 if is_return else 0,
            "orig_receipt_no": "",
            "items": items,
        })
    return out


def _payment_label(r, is_return: bool, is_nasiya: bool) -> str:
    """Chek qaysi to'lov turida — admin ilovasidagi «To'lov turlari» uchun.

    Bir chekda bir nechta tur bo'lishi mumkin (naqd + click + qarz) — bunday
    holat `split` deb belgilanadi, qismlari `paid_*` maydonlarida qoladi.
    """
    if is_return:
        return "qaytarish"
    parts = {
        "cash": abs(float(r["paid_cash"] or 0)),
        "card": abs(float(r["paid_card"] or 0)),
        "click": abs(float(r["paid_click"] or 0)),
        "qarz": abs(float(r["paid_debt"] or 0)),
    }
    used = [k for k, v in parts.items() if v > 0]
    if len(used) > 1:
        return "split"
    if used:
        return used[0]
    return "qarz" if is_nasiya else "cash"


def stats_blocks(day: str, month: str) -> dict:
    """`/api/stats` uchun: kun va oy bo'yicha HAQIQIY aylanma/foyda.

    Javob `web_api.api_stats` dagi `block()` bilan bir xil kalitlarni
    ishlatadi — u yerdagi qiymatlar ustidan yoziladi, shakl o'zgarmaydi.
    To'lov turlari (naqd/karta/click) cheklarda ajratilmagan, shuning uchun
    ularga TEGILMAYDI — eski manbadan qanday kelsa shunday qoladi.

    Cheklar yo'q bo'lsa `{}` qaytaradi — chaqiruvchi eski qiymatni saqlaydi.
    """
    costs = _product_costs()
    with _db() as mdb:
        c = mdb.conn
        try:
            if not c.execute("SELECT 1 FROM receipts LIMIT 1").fetchone():
                return {}
        except Exception:
            return {}

        out = {}
        known = unknown = 0.0
        for key, where, param in (("today", "day = ?", day),
                                  ("month", "day LIKE ?", f"{month}%")):
            head = c.execute(
                f"SELECT COUNT(*) n, SUM(payable) rev, "
                f"       SUM(CASE WHEN kind='return' THEN 1 ELSE 0 END) rn, "
                f"       SUM(COALESCE(paid_cash,0)) cash, "
                f"       SUM(COALESCE(paid_card,0)) card, "
                f"       SUM(COALESCE(paid_click,0)) click, "
                f"       SUM(COALESCE(paid_debt,0)) debt, "
                f"       SUM(CASE WHEN kind='return' THEN -payable ELSE 0 END) rsum "
                f"  FROM receipts WHERE {where}", (param,)
            ).fetchone()
            rows = c.execute(
                f"SELECT i.product_id, i.qty, i.amount FROM receipt_items i "
                f"  JOIN receipts r ON r.msg_id = i.msg_id WHERE r.{where}", (param,)
            ).fetchall()

            profit = cost_sum = 0.0
            for r in rows:
                amount = float(r["amount"] or 0)
                cost = costs.get(int(r["product_id"] or 0))
                if cost:
                    qty_cost = float(r["qty"] or 0) * cost
                    profit += amount - qty_cost
                    cost_sum += qty_cost
                    known += amount
                else:
                    unknown += amount

            out[key] = {
                "sale_count": int(head["n"] or 0),
                "revenue_sum": float(head["rev"] or 0),
                "profit_sum": round(profit, 2),
                "cost_sum": round(cost_sum, 2),
                "return_count": int(head["rn"] or 0),
                "return_sum": float(head["rsum"] or 0),
                "cash_sum": float(head["cash"] or 0),
                "card_sum": float(head["card"] or 0),
                "click_sum": float(head["click"] or 0),
                "nasiya_sum": float(head["debt"] or 0),
            }

        top = c.execute(
            """SELECT i.name, SUM(i.qty) qty, SUM(i.amount) rev
                 FROM receipt_items i JOIN receipts r ON r.msg_id = i.msg_id
                WHERE r.day LIKE ? GROUP BY i.name
                ORDER BY rev DESC LIMIT 7""", (f"{month}%",)
        ).fetchall()

    out["top_products"] = [{"name": t["name"], "qty": float(t["qty"] or 0),
                            "revenue_sum": float(t["rev"] or 0)} for t in top]
    out["profit_coverage"] = round(100 * known / max(known + unknown, 1))
    return out


def _product_costs() -> dict:
    """{katalog product_id: tannarx} — `pos.db` dan.

    Ulanish: pos.db `products.id` == YesPOS `sku`, chek qatori esa katalog
    `products.id` ga bog'langan — shuning uchun sku orqali ko'prik quramiz.
    """
    import sqlite3

    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from marketplace import config

    sku_cost = {}
    try:
        pos = sqlite3.connect(f"file:{config.POS_DB}?mode=ro", uri=True)
        sku_cost = {int(i): float(c or 0) for i, c in
                    pos.execute("SELECT id, cost_price FROM products") if c}
        pos.close()
    except Exception:
        return {}

    out = {}
    try:
        with _db() as mdb:
            for pid, sku in mdb.conn.execute(
                    "SELECT id, sku FROM products WHERE sku <> ''"):
                if str(sku).isdigit() and int(sku) in sku_cost:
                    out[int(pid)] = sku_cost[int(sku)]
    except Exception:
        return {}
    return out


@SALES_ROUTES.get("/api/sales/receipt")
async def api_sales_receipt(request: web.Request):
    """Bitta chekning tovarlari. Ruxsat: 'stats'."""
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err

    auth, err = await _need_admin(request, _db_pos, "stats")
    if err is not None:
        return err

    try:
        msg_id = int(request.query.get("id") or 0)
    except ValueError:
        msg_id = 0
    if not msg_id:
        return _err("Chek ID noto'g'ri")

    try:
        with _db() as mdb:
            head = mdb.conn.execute(
                "SELECT doc_no, kind, cashier, client, sold_at, total, discount, "
                "payable FROM receipts WHERE msg_id = ?", (msg_id,)
            ).fetchone()
            if not head:
                return _err("Chek topilmadi", 404)
            items = mdb.conn.execute(
                "SELECT line_no, name, qty, price, amount, product_id "
                "FROM receipt_items WHERE msg_id = ? ORDER BY line_no", (msg_id,)
            ).fetchall()
    except Exception as e:
        return _err(f"Chek o'qilmadi: {e}")

    return web.json_response({
        "ok": True,
        "doc_no": head["doc_no"],
        "kind": _KIND.get(head["kind"], head["kind"]),
        "cashier": head["cashier"] or "",
        "client": (head["client"] or "").strip(),
        "at": head["sold_at"],
        "total": round(head["total"] or 0),
        "discount": round(head["discount"] or 0),
        "payable": round(head["payable"] or 0),
        "items": [{
            "name": i["name"],
            "qty": i["qty"],
            "price": round(i["price"] or 0),
            "amount": round(i["amount"] or 0),
            # 0 = katalogga bog'lanmagan (tahlilga tushmaydi) — ko'rinib tursin.
            "linked": bool(i["product_id"]),
        } for i in items],
    })


def setup(app: web.Application):
    app.add_routes(SALES_ROUTES)
