"""Kanalga yuboriladigan chiroyli postlar uchun format funksiyalari."""

from database._helpers import fmt_usd, fmt_sum



def _channel_footer() -> str:
    """Kanal postining tagiga qo'shiladigan o'zgarmas marketing/aloqa bloki.
    Ikki tilda (o'zbek · rus), aloqa raqamlari, yetkazib berish, manzil va
    hashtag — odamni jalb qilish uchun. Raqam/manzil .env'dan sozlanadi."""
    try:
        from bot.config import CONTACT_PHONE_1, CONTACT_PHONE_2, SHOP_LOCATION
    except Exception:
        CONTACT_PHONE_1, CONTACT_PHONE_2, SHOP_LOCATION = "", "", ""
    lines = [
        "\n━━━━━━━━━━━━━━━",
        "🚚 <b>Yetkazib berish bor</b> · Доставка есть",
        "✅ Chinor — <b>arzon va sifatli</b> · дёшево и качественно",
        "🛒 Buyurtma / Заказ:",
    ]
    if CONTACT_PHONE_1:
        lines.append(f"📞 {CONTACT_PHONE_1}")
    if CONTACT_PHONE_2:
        lines.append(f"📞 {CONTACT_PHONE_2}")
    if SHOP_LOCATION:
        lines.append(f"📍 {SHOP_LOCATION}")
    lines.append("#chinor #chinormarket")
    return "\n".join(lines)


def _fmt_product(p: dict, ai_desc: str = "") -> str:
    """Kanalga yuboriladigan mahsulot postining kaptioni.
    Faqat so'm narxi ko'rsatiladi. AI tavsifi (o'zbek + rus) bo'lsa qo'shiladi.
    Pastiga doimo aloqa/marketing bloki (_channel_footer) qo'shiladi.
    Barcode bor bo'lsa ko'rsatiladi. Telegram kaption chegarasi 1024 belgi —
    AI tavsifi juda uzun bo'lsa qisqartiriladi."""
    unit = p.get("unit", "dona")
    active = "" if p.get("is_active", 1) else "\n❌ <i>Nofaol</i>"
    sell_sum = float(p.get("sell_price", 0) or 0)
    price_line = f"💰 <b>{fmt_sum(sell_sum)}/{unit}</b>"

    # AI tavsifi (o'zbek + rus, emoji bilan) — yoki oddiy tavsif.
    desc = (ai_desc or "").strip()
    if len(desc) > 450:
        desc = desc[:450].rstrip() + "…"
    desc_line = f"\n\n{desc}" if desc else (
        f"\n📝 {p['description']}" if p.get("description") else ""
    )

    # Barcode (bor bo'lsa ko'rsatiladi)
    barcode = str(p.get("barcode") or "").strip()
    barcode_line = f"\n🔢 <code>{barcode}</code>" if barcode else ""

    # Mahsulot ID (tartib raqami)
    pid = p.get("id", "")
    pid_line = f"\n🆔 #{pid}" if pid else ""

    return (
        f"🛍 <b>{p['name']}</b>{desc_line}\n\n"
        f"{price_line}{barcode_line}{pid_line}{active}"
        f"{_channel_footer()}"
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


def _fmt_client_receipt(s: dict, c: dict) -> str:
    """Mijozga Telegram orqali yuboriladigan elektron chek.
    Sotuv mijoz nomiga bo'lsa avtonom yuboriladi (naqd/karta/nasiya — barchasi)."""
    lines = "".join(
        f"  • {i['name']} — {i['qty']:g} {i.get('unit','dona')} × "
        f"{i['price']:,.0f} = {i['total']:,.0f}\n"
        for i in s.get("items", [])
    )
    if s.get("is_nasiya"):
        debt_now = float(c.get("debt", 0) or 0)
        pay_line = "🤝 To'lov turi: <b>Nasiya (qarzga)</b>"
        extra = f"\n💳 Joriy qarzingiz: <b>{fmt_sum(debt_now)}</b>"
    else:
        parts = []
        if float(s.get("paid_cash", 0) or 0) > 0:
            parts.append("Naqd")
        if float(s.get("paid_card", 0) or 0) > 0:
            parts.append("Karta")
        if float(s.get("paid_other", 0) or 0) > 0:
            parts.append("Boshqa")
        pay_line = f"💰 To'lov turi: <b>{', '.join(parts) or 'Naqd'}</b>"
        extra = ""
    no = s.get("receipt_no") or s.get("id") or ""
    change = ""
    if float(s.get("change", 0) or 0) > 0:
        change = f"\n💱 Qaytim: <b>{s.get('change', 0):,.0f} so'm</b>"
    return (
        f"🧾 <b>CHINOR — Elektron chek</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {c.get('shop_name','')}\n"
        f"№ {no}  ·  {str(s.get('created_at',''))[:16]}\n\n"
        f"{lines}\n"
        f"💵 <b>Jami: {s.get('total', 0):,.0f} so'm</b>\n"
        f"{pay_line}{extra}{change}\n\n"
        f"Xaridingiz uchun rahmat! 🙏"
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


def _fmt_cash_movement(m: dict) -> str:
    is_in = (m.get("direction") or "in") == "in"
    icon = "➕" if is_in else "➖"
    title = "Naqd kirim" if is_in else "Naqd chiqim"
    amount = float(m.get("amount", 0) or 0)
    lines = [
        f"#kassa 🆔{m['id']}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{icon} <b>{title} #{m['id']}</b>",
        f"💰 <b>{fmt_sum(amount)}</b>",
    ]
    if m.get("category"):
        lines.append(f"🏷 {m['category']}")
    if not is_in and m.get("recipient"):
        lines.append(f"👤 Kim oldi: {m['recipient']}")
    lines.append(f"📝 {m.get('note') or '—'}")
    if m.get("cashier_name"):
        lines.append(f"🧑‍💼 Kassir: {m['cashier_name']}")
    if m.get("source"):
        lines.append(f"🖥 {m['source']}")
    lines.append(f"📅 {(m.get('created_at') or '')[:16]}")
    return "\n".join(lines)


def _fmt_admin(a: dict) -> str:
    return (
        f"#admin 🆔{a['telegram_id']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👨‍💼 <b>{a.get('full_name', '?')}</b>\n"
        f"🆔 <code>{a['telegram_id']}</code>"
    )
