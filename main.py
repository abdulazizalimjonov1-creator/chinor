import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web as aioweb

from bot.config import (
    BOT_TOKEN, GLAVNIY_ADMIN_ID, CHANNEL_ID,
    API_ENABLED, API_HOST, API_PORT,
)
from bot.notifier import make_low_stock_handler
from bot.web_api import create_app as create_api_app
from database.channel_db import db, DB_PATH
from handlers import (
    glavniy, admin_products, admin_clients, admin_stats, sale, client, catalog,
    ai_analytics, auth_setup,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot):
    # Bot username'ni olib DB ga uzatamiz — kanal postidagi 'Sotib olish'
    # tugmasi t.me/{username}?start=buy_X chuqur havolasi uchun kerak.
    username = ""
    try:
        me = await bot.get_me()
        username = (me.username or "").lstrip("@")
        logger.info(f"Bot username: @{username}")
    except Exception as e:
        logger.error(f"bot.get_me() xato: {e}")
    db.set_bot(bot, username=username)
    db.set_low_stock_alert(make_low_stock_handler(bot))

    # Bosh admin uchun admins jadvalida qator borligini ta'minlaymiz —
    # u login/parol yaratishi, rol/ruxsatlari to'g'ri ko'rinishi uchun zarur.
    try:
        glavniy_name = "Bosh admin"
        try:
            ch = await bot.get_chat(GLAVNIY_ADMIN_ID)
            glavniy_name = ch.full_name or ch.title or glavniy_name
        except Exception:
            pass
        await db.ensure_admin_row(GLAVNIY_ADMIN_ID, glavniy_name, role="full")
        logger.info(f"Bosh admin qatori tayyor: {GLAVNIY_ADMIN_ID}")
    except Exception as e:
        logger.error(f"ensure_admin_row(glavniy) xato: {e}")
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        logger.info(f"Kanal: {chat.title} ({chat.id})")
    except Exception as e:
        logger.error(f"Kanal topilmadi: {e}  →  CHANNEL_ID ni tekshiring: {CHANNEL_ID}")
    try:
        await bot.send_message(
            GLAVNIY_ADMIN_ID,
            (
                "✨━━━━━━━━━━━━━━━━━━━━━✨\n"
                "🚀 <b>POS BOT ISHGA TUSHDI!</b>\n"
                "✨━━━━━━━━━━━━━━━━━━━━━✨\n\n"
                "👨‍💻 <b>@alinnjonov</b> tomonidan yaratilgan\n\n"
                f"📢 Kanal:    <code>{CHANNEL_ID}</code>\n"
                f"🤖 Bot:      <code>@{username or '?'}</code>\n\n"
                "🟢 <i>Tayyor! Buyruq kuting...</i>"
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass

    # Avtomatik backup o'chirilgan — qo'lda Eksport orqali yuklab olinadi.


async def main():
    if not all([BOT_TOKEN, GLAVNIY_ADMIN_ID, CHANNEL_ID]):
        logger.error(".env faylini tekshiring: BOT_TOKEN, GLAVNIY_ADMIN_ID, CHANNEL_ID kerak!")
        return

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.startup.register(on_startup)

    # ── Handlerlar (tartib muhim!) ────────────────────────────────────────────
    dp.include_router(glavniy.router)         # /start + bosh admin
    dp.include_router(admin_products.router)  # mahsulotlar
    dp.include_router(admin_clients.router)   # mijozlar
    dp.include_router(admin_stats.router)     # statistika
    dp.include_router(sale.router)            # sotuv paneli
    dp.include_router(client.router)          # klientlar
    dp.include_router(catalog.router)         # kategoriya / yetkazib beruvchi
    dp.include_router(ai_analytics.router)    # 🤖 AI Analitika (Gemini)
    dp.include_router(auth_setup.router)      # 🔑 Mini App login/parol yaratish

    logger.info(f"Bot ishga tushdi | Admin: {GLAVNIY_ADMIN_ID} | Kanal: {CHANNEL_ID}")
    logger.info(f"SQLite baza: {DB_PATH}")

    # ── HTTPS API serveri (Mini App login uchun) ────────────────────────
    # Bot bilan bir vaqtda ishlaydi. Frontend (Netlify) shu manzilga
    # POST /api/login yuboradi va javobiga qarab panel ochadi.
    api_runner = None
    if API_ENABLED:
        try:
            api_app = create_api_app()
            api_runner = aioweb.AppRunner(api_app)
            await api_runner.setup()
            site = aioweb.TCPSite(api_runner, API_HOST, API_PORT)
            await site.start()
            logger.info(f"🌐 API server: http://{API_HOST}:{API_PORT}")
            logger.info(f"   /api/health  va  /api/login  endpoint'lari ochiq")
        except Exception as e:
            logger.error(f"API serverni ishga tushirishda xato: {e}")
            api_runner = None
    else:
        logger.info("API server o'chirilgan (API_ENABLED=0)")

    try:
        # Endi channel_post tinglash kerak emas — ma'lumot SQLite da
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query"]
        )
    finally:
        await bot.session.close()
        if api_runner is not None:
            try:
                await api_runner.cleanup()
            except Exception as e:
                logger.error(f"API cleanup xato: {e}")
        # Bazaga doimiy ulanish va ishchi oqimni toza yopish
        try:
            db.shutdown()
        except Exception as e:
            logger.error(f"db.shutdown() xato: {e}")


if __name__ == "__main__":
    asyncio.run(main())
