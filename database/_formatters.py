"""Kanalga yuboriladigan chiroyli postlar uchun format funksiyalari."""

from database._helpers import fmt_usd, fmt_sum



def _fmt_product(p: dict) -> str:
    """Kanalga yuboriladigan mahsulot postining kaptioni —
    FAQAT dona narxi ko'rinadi. Optom narxi (agar bor bo'lsa) bot
    orqali — faqat 'optom' turidagi mijozlarga ko'rsatiladi.
    Narx — USD da, qavs ichida joriy kurs bo'yicha so'm ko'rsatiladi."""
    desc = f"\n📝 {p['description']}" if p.get("description") else ""
    unit = p.get("unit", "dona")
    active = "" if p.get("is_active", 1) else "\n❌ <i>Nofaol</i>"
    sell_usd = float(p.get("sell_price_usd", 0) or 0)
    sell_sum = float(p.get("sell_price", 0) or 0)
    if sell_usd > 0:
        price_line = f"💰 <b>{fmt_usd(sell_usd)}/{unit}</b>  (≈ {fmt_sum(sell_sum)})"
    else:
        price_line = f"💰 <b>{fmt_sum(sell_sum)}/{unit}</b>"
    return (
        f"📦 <b>{p['name']}</b>{desc}\n"
        f"{price_line}{active}"
    )


def _fmt_client(c: dict) -> str:
    debt_usd = float(c.get("debt_usd", 0) or 0)
    debt_sum = float(c.get("debt", 0) or 0)
    if debt_usd > 0 or debt_sum > 0:
        if debt_usd > 0:
            debt_line = f"\n💳 Qarz: <b>{fmt_usd(debt_usd)}</b>  (≈ {fmt_sum(debt_sum)})"
        else:
            debt_line = f"\n💳 Qarz: <b>{fmt_sum(debt_sum)}</b>"
    else:
        debt_line = "\n✅ Qarzsiz"
    return (
        f"#mijoz 🆔{c['id']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{c['shop_name']}</b>\n"
        f"📱 {c.get('phone','')}{debt_line}\n"
        f"📅 {c['created_at'][:10]}"
    )


def _fmt_order(o: dict) -> str:
    from bot.config import ORDER_STATUSES
    lines = "".join(
        f"  • {i['name']}: {i['qty']:g} × {i['price']:,.0f} = {i['total']:,.0f} so'm\n"
        for i in o.get("items", [])
    )
    status = ORDER_STATUSES.get(o.get("status", ""), o.get("status", ""))
    return (
        f"#buyurtma 🆔{o['id']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🚚 <b>Buyurtma #{o['id']}</b> — {status}\n"
        f"👤 {o.get('shop_name','')}  |  📱 {o.get('phone','')}\n\n"
        f"{lines}\n"
        f"💰 Jami: <b>{o['total']:,.0f} so'm</b>\n"
        f"📅 {o['created_at'][:16]}"
    )


def _fmt_sale(s: dict) -> str:
    lines = "".join(
        f"  {i['name']}\n  {i['qty']:g} {i.get('unit','dona')} × {i['price']:,.0f} = {i['total']:,.0f} so'm\n"
        for i in s.get("items", [])
    )
    paid_parts = []
    if s.get("is_nasiya"):
        client_name = s.get("client_name", "")
        paid_parts.append(f"🤝 Nasiya ({client_name}): {s['total']:,.0f} so'm — QARZGA YOZILDI")
    else:
        if s.get("paid_cash", 0) > 0:
            paid_parts.append(f"💵 Naqd: {s['paid_cash']:,.0f} so'm")
        if s.get("paid_card", 0) > 0:
            paid_parts.append(f"💳 Karta: {s['paid_card']:,.0f} so'm")
        if s.get("paid_other", 0) > 0:
            paid_parts.append(f"🔄 Boshqa: {s['paid_other']:,.0f} so'm")
    paid_txt = "\n".join(paid_parts)
    change = f"\n💱 Qaytim: <b>{s.get('change',0):,.0f} so'm</b>" if s.get("change", 0) > 0 else ""
    return (
        f"#sotuv 🆔{s['id']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧾 <b>Chek #{s['id']}</b>\n"
        f"👤 {s.get('cashier_name', '?')}\n"
        f"📅 {s['created_at'][:16]}\n\n"
        f"{lines}\n"
        f"💰 <b>Jami: {s['total']:,.0f} so'm</b>\n"
        f"{paid_txt}{change}"
    )


def _fmt_payment(p: dict) -> str:
    amount = float(p.get("amount", 0) or 0)
    amount_usd = float(p.get("amount_usd", 0) or 0)
    cur = (p.get("currency") or "sum").lower()
    if cur == "usd" and amount_usd > 0:
        money_line = f"💰 <b>{fmt_usd(amount_usd)}</b>  (≈ {fmt_sum(amount)})"
    elif amount_usd > 0:
        money_line = f"💰 <b>{fmt_sum(amount)}</b>  (≈ {fmt_usd(amount_usd)})"
    else:
        money_line = f"💰 <b>{fmt_sum(amount)}</b>"
    return (
        f"#tolov 🆔{p['id']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>To'lov #{p['id']}</b>\n"
        f"👤 {p['shop_name']}\n"
        f"{money_line}\n"
        f"📝 {p.get('note') or '—'}\n"
        f"📅 {p['created_at'][:16]}"
    )


def _fmt_admin(a: dict) -> str:
    return (
        f"#admin 🆔{a['telegram_id']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👨‍💼 <b>{a.get('full_name', '?')}</b>\n"
        f"🆔 <code>{a['telegram_id']}</code>"
    )
