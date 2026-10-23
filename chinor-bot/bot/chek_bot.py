"""«Cheklar boti» (@cheklars_bot) — kassa kompyuteridan ko'chirilgan.

NIMA EDI. Bu bot do'kon kassasidagi kompyuterda ishlardi: mijoz raqamini
tasdiqlardi, keyin xaridlari va qarzi haqidagi xabarni shu botdan olardi.
Uning butun mantig'i endi serverda — cheklar ham, qarz daftari ham shu
yerda (`marketplace/debt.py`).

NIMA UCHUN ALOHIDA BOT, ASOSIYSIGA QO'SHIB YUBORMAYMIZ. Do'konning ko'p
mijozi aynan shu botga /start bosgan va cheklarini shu yerdan kutadi.
Botni almashtirsak, ular yangi botni qidirib topishi kerak bo'lardi.
Shuning uchun token serverga ko'chadi, bot esa o'z nomida qolaveradi.

NIMA QILADI — ataylab oz:
  • /start → raqamni tugma bilan tasdiqlash;
  • raqam kassadagi mijoz bilan mos tushsa — hisob bog'lanadi va
    qarzi aytiladi;
  • Mini App tugmasi: xaridlar, cheklar, hisob-kitob o'sha yerda.

Xabar YUBORISHNI o'zi boshlamaydi: cheklarni admin Mini App'dan
jo'natadi (`api_debt.py`). Bu ataylab — bot o'zicha reklama yozmaydi.

DIQQAT: bitta tokenni ikki dastur o'qisa, Telegram xabarlarni ularga
NAVBAT bilan beradi va ikkalasi ham yarmini yo'qotadi. Shuning uchun bu
bot ishga tushishidan oldin kassa kompyuteridagi nusxa to'xtatilgan
bo'lishi SHART.
"""

import asyncio
import json
import logging

import aiohttp

from .config import CHEK_BOT_TOKEN, CONTACT_PHONE_1, MINI_APP_URL

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_last_update_id = 0


def is_enabled() -> bool:
    return bool(CHEK_BOT_TOKEN)


async def _call(session, method: str, payload: dict) -> dict:
    url = _API.format(token=CHEK_BOT_TOKEN, method=method)
    try:
        async with session.post(url, json=payload) as r:
            return await r.json()
    except Exception as e:
        logger.info("🧾 Cheklar boti — %s xatosi: %s", method, e)
        return {}


def _contact_kb() -> dict:
    return {
        "keyboard": [[{"text": "📞 Raqamni tasdiqlash", "request_contact": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _app_kb() -> dict:
    """Mini App'ga INLINE tugma.

    DIQQAT: oddiy klaviatura (reply keyboard) tugmasidan ochilgan Mini
    App'ga Telegram IMZO BERMAYDI — `initData` bo'sh keladi va ilova
    foydalanuvchini tanimay «avtorizatsiya kerak» deydi. Imzo faqat
    menyu tugmasi, inline tugma va to'g'ridan-to'g'ri havoladan
    ochilganda beriladi. Shuning uchun bu yerda inline tugma, qo'shimcha
    ravishda `_set_menu_button()` matn maydoni yonidagi doimiy tugmani
    ham o'rnatadi.
    """
    if not MINI_APP_URL.startswith("https://"):
        return {"remove_keyboard": True}
    return {"inline_keyboard": [[{"text": "🛒 Do'kon va hisobim",
                                  "web_app": {"url": MINI_APP_URL}}]]}


async def _set_menu_button(session, chat_id) -> None:
    """Matn maydoni yonidagi doimiy menyu tugmasi — Mini App'ni ochadi.

    Undan ochilgan Mini App IMZOLANGAN bo'ladi, shuning uchun mijoz
    qaytadan hech narsa kiritmaydi. Har bog'lanishda qayta o'rnatiladi:
    manzil o'zgarsa, Telegram'da saqlanib qolgan eski tugma o'z-o'zini
    tuzatadi.
    """
    if not MINI_APP_URL.startswith("https://"):
        return
    await _call(session, "setChatMenuButton", {
        "chat_id": chat_id,
        "menu_button": {"type": "web_app", "text": "🛒 Do'kon",
                        "web_app": {"url": MINI_APP_URL}},
    })


async def _send(session, chat_id, text: str, kb: dict = None) -> bool:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if kb is not None:
        payload["reply_markup"] = kb
    return bool((await _call(session, "sendMessage", payload)).get("ok"))


# ─── Mijozni kassadagi hisobga bog'lash ─────────────────────────────────────

def _bind(phone: str, chat_id: int) -> tuple:
    """(nomlar, qarz) — kassadagi hisobni shu chatga bog'laydi.

    Hech narsa topilmasa ([], 0): mijoz kassada hali xarid qilmagan yoki
    raqami boshqacha yozilgan. Bu xato emas — shunchaki aytiladi.
    """
    import os
    import sys

    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from marketplace import debt as D
    from marketplace.db import MarketplaceDB

    with MarketplaceDB() as mdb:
        D.ensure_schema(mdb.conn)
        names = D.bind_phone(mdb.conn, phone, chat_id)
        total = sum(D.debt(mdb.conn, n) for n in names)
        mdb.conn.commit()
    return names, total


async def ensure_shop_client(tg_id: int, phone: str, name: str) -> bool:
    """Mijoz `pos.db` da ham bo'lsin — Mini App aynan shuni talab qiladi.

    NEGA KERAK: Mini App foydalanuvchini `clients` jadvalidan topadi.
    Kassadagi qarz daftari boshqa bazada (`marketplace.db`), shuning
    uchun cheklar botida raqamini tasdiqlagan odam Mini App'ga kirmoqchi
    bo'lsa «avtorizatsiya kerak» olardi — imzo to'g'ri bo'lsa ham.

    Bor mijozga TEGILMAYDI: faqat Telegram ID si bo'sh bo'lsa biriktiriladi.
    """
    from database.channel_db import db

    try:
        if await db.get_client_by_tg(tg_id):
            return True
        found = await db.get_client_by_phone(phone)
        if found:
            if not found.get("telegram_id"):
                await db.set_client_tg_id(found["id"], tg_id)
            return True
        shop = (name or "").strip() or f"Mijoz {str(phone)[-4:]}"
        await db.add_client(tg_id=tg_id, shop_name=shop[:80], phone=phone,
                            registered_by=tg_id, client_type="dona")
        return bool(await db.get_client_by_tg(tg_id))
    except Exception as e:
        logger.warning("🧾 pos.db mijozi yaratilmadi (%s): %s", tg_id, e)
        return False


def _money(v) -> str:
    return f"{round(float(v or 0)):,}".replace(",", " ")


WELCOME = (
    "Assalomu alaykum! 👋\n\n"
    "Bu <b>Chinor</b> do'koni cheklarini yuboradigan bot.\n\n"
    "Xaridlaringiz va hisob-kitobingizni ko'rish uchun pastdagi "
    "<b>«📞 Raqamni tasdiqlash»</b> tugmasini bosing."
)

HELP = (
    "Raqamingizni tasdiqlang — xaridlaringiz va qarzingiz ko'rinadi.\n\n"
    "/start — ulanish\n"
    "/help — yordam"
)


async def _on_contact(session, msg: dict) -> None:
    contact = msg.get("contact") or {}
    frm = msg.get("from") or {}
    chat_id = (msg.get("chat") or {}).get("id")

    # Faqat O'Z raqamini qabul qilamiz — boshqasinikini emas.
    if contact.get("user_id") and frm.get("id") \
            and contact["user_id"] != frm["id"]:
        await _send(session, chat_id,
                    "Iltimos, <b>o'z</b> raqamingizni tugma orqali yuboring.",
                    _contact_kb())
        return

    phone = (contact.get("phone_number") or "").strip()
    if not phone:
        await _send(session, chat_id,
                    "Raqamni o'qiy olmadim. Yana urinib ko'ring.",
                    _contact_kb())
        return

    try:
        names, total = await asyncio.get_running_loop().run_in_executor(
            None, _bind, phone, chat_id)
    except Exception as e:
        logger.warning("🧾 Hisob bog'lanmadi: %s", e)
        names, total = [], 0

    # Mini App `pos.db` dagi mijozni talab qiladi — kassadagi nom bilan
    # (bo'lmasa Telegram ismi bilan) yaratamiz.
    nick = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)
    await ensure_shop_client(chat_id, phone, names[0] if names else nick)

    # Imzolangan kirish nuqtasi: matn maydoni yonidagi doimiy tugma.
    await _set_menu_button(session, chat_id)

    if not names:
        await _send(
            session, chat_id,
            "✅ Raqamingiz tasdiqlandi.\n\n"
            "Lekin do'kon kassasida bu raqamga yozilgan hisob topilmadi. "
            "Agar xaridlaringiz boshqa raqamga yozilgan bo'lsa, "
            f"{CONTACT_PHONE_1} ga qo'ng'iroq qiling.\n\n"
            "Katalogni hoziroq ko'rishingiz mumkin 👇", _app_kb())
        return

    qarz = (f"📕 Qarzingiz: <b>{_money(total)}</b> so'm" if total > 0
            else "✅ Qarzingiz yo'q")
    await _send(
        session, chat_id,
        f"✅ Raqamingiz tasdiqlandi.\n\n"
        f"🧾 Kassadagi hisobingiz: <b>{', '.join(names)}</b>\n{qarz}\n\n"
        f"Xaridlaringiz, cheklaringiz va hisob-kitob daftaringiz "
        f"Mini App'da — <b>«Mening hisobim»</b> bo'limida. "
        f"Do'konga borish shart emas 👇", _app_kb())
    logger.info("🧾 Cheklar boti: %s → %s", chat_id, ", ".join(names))


async def _on_message(session, msg: dict) -> None:
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id:
        return
    if msg.get("contact"):
        await _on_contact(session, msg)
        return

    text = (msg.get("text") or "").strip()
    if text.startswith("/start"):
        await _set_menu_button(session, chat_id)
        await _send(session, chat_id, WELCOME, _contact_kb())
    elif text.startswith("/help") or text.startswith("/yordam"):
        await _send(session, chat_id, HELP, _contact_kb())
    else:
        # Raqami tasdiqlangan bo'lsa Mini App tugmasini beramiz, aks holda
        # tasdiqlashni so'raymiz.
        try:
            names = await asyncio.get_running_loop().run_in_executor(
                None, _names_of, chat_id)
        except Exception:
            names = []
        if names:
            await _send(session, chat_id,
                        "Xaridlaringiz va hisob-kitobingiz Mini App'da 👇",
                        _app_kb())
        else:
            await _send(session, chat_id,
                        "Boshlash uchun raqamingizni tasdiqlang 👇",
                        _contact_kb())


def _names_of(chat_id) -> list:
    import os
    import sys

    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from marketplace import debt as D
    from marketplace.db import MarketplaceDB

    with MarketplaceDB() as mdb:
        D.ensure_schema(mdb.conn)
        return D.client_by_chat(mdb.conn, str(chat_id))


async def poll_loop(timeout_s: int = 50) -> None:
    """Uzun so'rov (long polling). Asosiy botdan mustaqil ishlaydi."""
    global _last_update_id
    if not is_enabled():
        logger.info("🧾 Cheklar boti o'chiq: CHEK_BOT_TOKEN yo'q")
        return

    async with aiohttp.ClientSession() as session:
        me = await _call(session, "getMe", {})
        uname = ((me.get("result") or {}).get("username") or "?")
        logger.info("🧾 Cheklar boti ishga tushdi: @%s", uname)

        # UMUMIY menyu tugmasi — barcha shaxsiy suhbatlar uchun sukut
        # qiymati. Shu bilan eski mijozlar /start bosmasdan ham Mini
        # App'ni matn maydoni yonidagi tugmadan IMZOLANGAN holda
        # ochadi (klaviatura tugmasidan ochilsa imzo bo'lmaydi).
        if MINI_APP_URL.startswith("https://"):
            r = await _call(session, "setChatMenuButton", {
                "menu_button": {"type": "web_app", "text": "🛒 Do'kon",
                                "web_app": {"url": MINI_APP_URL}}})
            logger.info("🧾 Menyu tugmasi: %s",
                        "o'rnatildi" if r.get("ok") else r.get("description"))
        else:
            logger.warning("🧾 MINI_APP_URL https:// emas — menyu tugmasi yo'q")

        while True:
            try:
                url = _API.format(token=CHEK_BOT_TOKEN, method="getUpdates")
                params = {"offset": _last_update_id + 1, "timeout": timeout_s,
                          "allowed_updates": json.dumps(["message"])}
                async with session.get(
                        url, params=params,
                        timeout=aiohttp.ClientTimeout(total=timeout_s + 20)) as r:
                    data = await r.json()
                if not data.get("ok"):
                    # 409 = o'sha tokenni boshqa dastur ham o'qiyapti.
                    # Jim qolmaymiz: bu holat xabarlarning yo'qolishini
                    # bildiradi va darhol hal qilinishi kerak.
                    desc = str(data.get("description") or "")
                    if "409" in desc or "terminated by other" in desc.lower():
                        logger.warning(
                            "🧾 DIQQAT: @cheklars_bot tokenini boshqa dastur "
                            "ham o'qiyapti (kassa kompyuteridagi nusxa "
                            "to'xtatilmagan). Xabarlar yo'qoladi!")
                    await asyncio.sleep(5)
                    continue
                for upd in (data.get("result") or []):
                    _last_update_id = max(_last_update_id,
                                          int(upd.get("update_id") or 0))
                    msg = upd.get("message")
                    if msg:
                        try:
                            await _on_message(session, msg)
                        except Exception as e:
                            logger.warning("🧾 Xabar ishlanmadi: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.info("🧾 Cheklar boti — sikl xatosi: %s", e)
                await asyncio.sleep(5)


async def backfill() -> int:
    """Eski botda ro'yxatdan o'tganlar uchun `pos.db` mijozini yaratadi.

    Kassadagi bot mijozni faqat o'z bazasida saqlardi. Ular Mini App'ga
    kira olishi uchun `clients` jadvalida ham bo'lishi kerak — bu
    bir martalik to'ldirish. Qayta ishga tushirish xavfsiz: bor
    mijozga tegilmaydi.

    Ishlatish:  python -m bot.chek_bot backfill
    """
    import os
    import sys as _sys

    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if root not in _sys.path:
        _sys.path.insert(0, root)
    from marketplace import debt as D
    from marketplace.db import MarketplaceDB

    with MarketplaceDB() as mdb:
        D.ensure_schema(mdb.conn)
        rows = mdb.conn.execute(
            "SELECT name, chat_id, phone, phone2 FROM debt_clients "
            "WHERE chat_id <> '' ORDER BY name").fetchall()

    done = skipped = 0
    for r in rows:
        phone = (r["phone"] or r["phone2"] or "").strip()
        if not phone:
            # Telefonsiz mijozni yarata olmaymiz — `clients` da telefon
            # kalit maydon. Kassada raqami yo'q, admin qo'lda kiritadi.
            print(f"  ? {r['name']}: telefon yo'q — o'tkazildi")
            skipped += 1
            continue
        ok = await ensure_shop_client(int(r["chat_id"]), phone, r["name"])
        print(("  + " if ok else "  ! ") + f"{r['name']}  {phone}")
        done += 1 if ok else 0
    print(f"\nTayyor: {done} ta mijoz Mini App'ga kira oladi, "
          f"{skipped} ta o'tkazildi")
    return 0


if __name__ == "__main__":
    import sys as _s

    if len(_s.argv) > 1 and _s.argv[1] == "backfill":
        raise SystemExit(asyncio.run(backfill()))
    raise SystemExit("Ishlatish: python -m bot.chek_bot backfill")
