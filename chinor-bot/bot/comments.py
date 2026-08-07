"""Kanal postiga IZOH (comment) yozish — muhokama guruhi orqali.

Telegram'da kanal posti ostidagi «izohlar» aslida ALOHIDA guruhdagi
xabarlardir. Kanalga bog'langan muhokama guruhi bo'lsa, har bir post o'sha
guruhga avtomatik ko'chiriladi; o'sha nusxaga JAVOB yozilsa — u post ostida
izoh bo'lib ko'rinadi.

Shuning uchun bot postni yuborgach javob xabarni darrov yubora olmaydi: avval
guruhdagi nusxa kelishini kutish kerak. Tartib shunday:

  1. `queue(channel_msg_id, matn)` — post yuborilgandan keyin chaqiriladi.
  2. Telegram postni muhokama guruhiga ko'chiradi → botga `is_automatic_forward`
     xabari keladi → `on_auto_forward()` uni tutadi va izohni yuboradi.

Ko'chirma navbatdan OLDIN kelib qolishi ham mumkin (tarmoq tezligiga bog'liq),
shuning uchun ikkala tomon ham eslab turadi va kim keyin kelsa — o'sha yuboradi.

Kanalda muhokama guruhi ULANMAGAN bo'lsa izoh yozib bo'lmaydi (Telegram
cheklovi): matn bazada qoladi (Mini App va saytda ko'rinadi), kanalga esa
hech narsa chiqmaydi. Guruh ulangan zahoti o'zi ishlay boshlaydi.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

TTL = 600          # navbatdagi matn shuncha soniyadan keyin unutiladi

_pending: dict = {}      # kanal_msg_id → (matn, vaqt)
_forwards: dict = {}     # kanal_msg_id → (guruh_chat_id, guruh_msg_id, vaqt)


def _sweep(store: dict) -> None:
    now = time.time()
    for k in [k for k, v in store.items() if now - v[-1] > TTL]:
        store.pop(k, None)


async def queue(bot, channel_msg_id: int, text: str, on_sent=None) -> None:
    """Post ostiga yoziladigan izohni navbatga qo'yadi (yoki darrov yuboradi).

    `on_sent(chat_id, msg_id)` — izoh yozilgach chaqiriladi (koroutina ham
    bo'ladi). Chaqiruvchi shu orqali izoh manzilini eslab qoladi va keyingi
    qayta e'londa eskisini o'chiradi.
    """
    if not text or not channel_msg_id:
        return
    _sweep(_pending)
    _sweep(_forwards)
    got = _forwards.pop(channel_msg_id, None)
    if got:                       # ko'chirma allaqachon kelgan — kutish shart emas
        await _send(bot, got[0], got[1], text, channel_msg_id, on_sent)
        return
    _pending[channel_msg_id] = (text, on_sent, time.time())


async def on_auto_forward(message) -> None:
    """Muhokama guruhiga avtomatik ko'chirilgan post — izohni shunga javob qilamiz.

    `handlers/discussion.py` dan chaqiriladi.
    """
    src = _origin_msg_id(message)
    if not src:
        return
    _sweep(_pending)
    _sweep(_forwards)
    got = _pending.pop(src, None)
    if got is None:
        # Izoh hali tayyor emas (yoki bu post uchun izoh yo'q) — ko'chirmani
        # eslab qolamiz, `queue()` kelsa o'sha zahoti yuboriladi.
        _forwards[src] = (message.chat.id, message.message_id, time.time())
        return
    await _send(message.bot, message.chat.id, message.message_id,
                got[0], src, got[1])


def _origin_msg_id(message):
    """Ko'chirilgan xabardan ASL kanal xabari raqamini oladi.

    Bot API 7.0 dan `forward_origin` ishlatiladi; eski `forward_from_message_id`
    ham qoldirilgan — ikkalasi ham tekshiriladi.
    """
    origin = getattr(message, "forward_origin", None)
    mid = getattr(origin, "message_id", None) if origin is not None else None
    return mid or getattr(message, "forward_from_message_id", None)


async def _send(bot, chat_id: int, reply_to: int, text: str,
                channel_msg_id: int, on_sent=None) -> None:
    try:
        m = await bot.send_message(chat_id, text, parse_mode="HTML",
                                   reply_to_message_id=reply_to,
                                   disable_notification=True,
                                   disable_web_page_preview=True)
        logger.info("💬 Izoh yozildi (post %s)", channel_msg_id)
    except Exception as e:
        logger.warning("💬 Izoh yozilmadi (post %s): %s", channel_msg_id, e)
        return
    if on_sent is None:
        return
    try:
        res = on_sent(chat_id, m.message_id)
        if hasattr(res, "__await__"):
            await res
    except Exception as e:
        logger.warning("💬 Izoh manzili saqlanmadi: %s", e)


async def drop(bot, ref: str) -> None:
    """Ilgari yozilgan izohni o'chiradi. `ref` — "chat_id:msg_id".

    Post qayta e'lon qilinganda eskisi guruhda osilib qolmasin (yangi post
    yangi izoh oladi, eskisi esa endi hech qaysi postga tegishli emas).
    """
    try:
        chat_id, msg_id = str(ref or "").split(":")
        await bot.delete_message(int(chat_id), int(msg_id))
    except Exception:
        pass                       # allaqachon o'chgan bo'lsa ham mayli


def linked_chat_id(token: str, channel_id) -> int:
    """Kanalga bog'langan muhokama guruhi (0 — yo'q).

    aiogram'ning `get_chat` usuli bu kanalda YIQILADI: Telegram javobiga yangi
    maydon qo'shgan (`available_reactions: [{'type': 'paid'}]`) va aiogram 3.7
    uni tanimaydi. Shuning uchun javob to'g'ridan-to'g'ri o'qiladi — bizga
    faqat bitta raqam kerak. Bloklovchi: executor'da chaqiring.
    """
    try:
        import json
        import urllib.request
        url = (f"https://api.telegram.org/bot{token}/getChat"
               f"?chat_id={channel_id}")
        with urllib.request.urlopen(url, timeout=10) as r:
            return int(json.load(r)["result"].get("linked_chat_id") or 0)
    except Exception as e:
        logger.warning("💬 Muhokama guruhi aniqlanmadi: %s", e)
        return 0
