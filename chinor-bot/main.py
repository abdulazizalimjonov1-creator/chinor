import asyncio
import logging
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web as aioweb

from bot.config import (
    BOT_TOKEN, GLAVNIY_ADMIN_ID, CHANNEL_ID,
    API_ENABLED, API_HOST, API_PORT, MINI_APP_URL,
)
from bot.notifier import make_low_stock_handler
from bot.web_api import create_app as create_api_app
from bot import payment_relay
from database.channel_db import db, DB_PATH
from database._helpers import now_local
from handlers import (
    glavniy, admin_products, admin_clients, admin_stats, sale, client, catalog,
    ai_analytics, auth_setup, client_ai, discussion, kabinet, kanal_aniq,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def _sync_commands(bot: Bot):
    """«/» bosilganda chiqadigan buyruqlar ro'yxati.

    Ikki xil: oddiy mijozga qisqa va tushunarli ro'yxat, adminlarga esa
    ustiga qo'shimcha. Ro'yxat har ishga tushganda qayta yoziladi —
    buyruq qo'shilsa .env yoki qo'lda sozlash kerak bo'lmaydi.
    """
    from aiogram.types import BotCommand, BotCommandScopeChat

    mijoz = [
        BotCommand(command="start", description="🏠 Boshlash"),
        BotCommand(command="hisobim", description="💰 Qarzim va hisob holati"),
        BotCommand(command="xaridlarim", description="🧾 Oxirgi xaridlarim"),
        BotCommand(command="rasm", description="🖼 Hisobotni rasm qilib olish"),
        BotCommand(command="excel", description="📥 Hisob-kitob (Excel fayl)"),
        BotCommand(command="aloqa", description="📞 Do'kon manzili va telefoni"),
        BotCommand(command="yordam", description="ℹ️ Yordam"),
    ]
    try:
        await bot.set_my_commands(mijoz)
    except Exception as e:
        logger.warning(f"Buyruqlar ro'yxati o'rnatilmadi: {e}")
        return

    # Adminlarga — faqat o'z suhbatlarida (boshqalar ko'rmaydi).
    admin = mijoz + [
        BotCommand(command="qarzdorlar", description="📕 Qarzdorlar ro'yxati"),
        BotCommand(command="reels", description="📊 Instagram natijalari"),
    ]
    try:
        ids = {GLAVNIY_ADMIN_ID}
        for a in (await db.get_all_admins() or []):
            try:
                ids.add(int(a.get("telegram_id") or 0))
            except (TypeError, ValueError):
                pass
        for chat_id in ids:
            if not chat_id:
                continue
            try:
                await bot.set_my_commands(
                    admin, scope=BotCommandScopeChat(chat_id=chat_id))
            except Exception:
                # Admin botni bloklagan yoki hali /start bosmagan bo'lishi
                # mumkin — bu butun ro'yxatni to'xtatmasligi kerak.
                pass
    except Exception as e:
        logger.warning(f"Admin buyruqlari o'rnatilmadi: {e}")


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
    await _sync_commands(bot)

    # Mini App "menu tugmasi"ni MINI_APP_URL bilan sinxronlaymiz (yagona manba).
    # Shunda tunnel/domen o'zgarsa, faqat .env'ni yangilab botni qayta ishga
    # tushirish kifoya — Telegram'dagi menu tugmasi avtomatik to'g'rilanadi.
    try:
        if MINI_APP_URL.startswith("https://"):
            from aiogram.types import MenuButtonWebApp, WebAppInfo
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="chinor", web_app=WebAppInfo(url=MINI_APP_URL)
                )
            )
            logger.info(f"Mini App menu tugmasi o'rnatildi: {MINI_APP_URL}")
    except Exception as e:
        logger.warning(f"Menu tugmasini o'rnatishda xato: {e}")

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
    # Muhokama guruhi bormi — «qanday ishlatiladi» izohi shusiz yozilmaydi
    # (Telegram'da izoh = bog'langan guruhdagi javob xabar).
    try:
        from bot import comments
        loop = asyncio.get_running_loop()
        gid = await loop.run_in_executor(
            None, comments.linked_chat_id, BOT_TOKEN, CHANNEL_ID)
        if gid:
            logger.info(f"💬 Muhokama guruhi: {gid} — izohlar yoziladi")
        else:
            logger.warning(
                "💬 Kanalga muhokama guruhi ULANMAGAN — «qanday ishlatiladi» "
                "izohi yozilmaydi. Kanal sozlamalari → Muhokama → guruh "
                "ulang va botni o'sha guruhga admin qiling.")
    except Exception as e:
        logger.warning(f"💬 Muhokama guruhi tekshirilmadi: {e}")
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


async def _retention_loop(days: int = 90, batch: int = 500):
    """Cheklarni serverda `days` kun saqlaydi, keyin asta-sekin o'chiradi.
    Har soatda eski yozuvlarni kichik bo'laklarda (batch) tozalaydi — katta
    lock bo'lmaydi. Boshlanishida 30s kutadi (ishga tushish bilan to'qnashmasin)."""
    await asyncio.sleep(30)
    while True:
        try:
            cutoff = (now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            total = 0
            # to'liq tozalanguncha bo'laklab o'chiramiz (har bo'lak orasida nafas)
            while True:
                deleted = await db.purge_old_sales(cutoff, batch)
                total += deleted
                if deleted < batch:
                    break
                await asyncio.sleep(2)
            if total:
                logger.info(f"[retention] {total} ta eski chek o'chirildi (>{days} kun)")
        except Exception as e:
            logger.warning(f"[retention] xato: {e}")
        await asyncio.sleep(3600)   # har soatda bir tekshiradi


async def main():
    if not all([BOT_TOKEN, GLAVNIY_ADMIN_ID, CHANNEL_ID]):
        logger.error(".env faylini tekshiring: BOT_TOKEN, GLAVNIY_ADMIN_ID, CHANNEL_ID kerak!")
        return

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.startup.register(on_startup)

    # ── Handlerlar (tartib muhim!) ────────────────────────────────────────────
    # ENG BIRINCHI: muhokama guruhiga avtomatik ko'chirilgan kanal posti.
    # U hech qanday buyruq emas — boshqa handlerlar uni "mijoz matni" deb
    # o'ylab, AI sotuvchiga uzatib yubormasin.
    dp.include_router(discussion.router)      # 💬 post ostidagi izohlar
    dp.include_router(glavniy.router)         # /start + bosh admin
    dp.include_router(admin_products.router)  # mahsulotlar
    dp.include_router(admin_clients.router)   # mijozlar
    dp.include_router(admin_stats.router)     # statistika
    dp.include_router(sale.router)            # sotuv paneli
    dp.include_router(client.router)          # klientlar
    dp.include_router(catalog.router)         # kategoriya / yetkazib beruvchi
    dp.include_router(ai_analytics.router)    # 🤖 AI Analitika (Gemini)
    dp.include_router(auth_setup.router)      # 🔑 Mini App login/parol yaratish
    from handlers import reels_compare_admin
    dp.include_router(reels_compare_admin.router)  # 🎬 kunlik video tasdig'i
    from handlers import ig_social_admin
    dp.include_router(ig_social_admin.router)      # 💬 Instagram izoh/lichka javobi
    from handlers import idea_reel_admin
    dp.include_router(idea_reel_admin.router)      # 💡 ovozli g'oya → rolik
    dp.include_router(kabinet.router)         # 🧾 mijoz kabineti (/hisobim …)
    dp.include_router(kanal_aniq.router)      # 📢 kanal id sini aniqlash
    # ENG OXIRIDA: mijozning erkin matni → AI sotuvchi. Barcha aniq
    # ishlovchilar avval ishlaydi, bu yerga faqat "egasiz" matn tushadi.
    dp.include_router(client_ai.router)

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

    # ── Cheklar retention (3 oy) — fonda ishlaydi ───────────────────────
    retention_task = asyncio.create_task(_retention_loop(days=90))

    # ── To'lov-link relay (kassa QR to'lovi) — fonda to'lov botini kuzatadi ──
    payment_task = asyncio.create_task(payment_relay.poll_loop())

    # ── Cheklar boti (@cheklars_bot) — ilgari kassa kompyuterida edi.
    # Mijoz raqamini tasdiqlab, kassadagi hisobiga bog'lanadi va Mini
    # App'ni ochadi. Token bo'lmasa o'zi jim o'chadi.
    from bot import chek_bot
    chek_bot_task = asyncio.create_task(chek_bot.poll_loop())

    # ── Kunlik «savol → taqqoslash» videosi (adminga tasdiq uchun) ──────
    from bot import reels_compare
    reels_task = asyncio.create_task(reels_compare.daily_loop())

    # Tovar roliklari: navbat ishchisi + kuniga 2 ta avto-tanlov (Instagram)
    from bot import reels as reels_mod
    reels_worker_task = asyncio.create_task(reels_mod.worker_loop())
    reels_pick_task = asyncio.create_task(reels_mod.autopick_loop())

    # ── Kunlik avtomatik zaxira (baza → data/backups + bosh adminga) ────
    from bot.backup import auto_backup_loop
    backup_task = asyncio.create_task(auto_backup_loop(bot))

    # ── YesPOS cheklari (userbot, har 30 daqiqa) → haqiqiy savdo tarixi ──
    from bot import receipts_sync
    receipts_task = asyncio.create_task(receipts_sync.loop())

    # ── Qarzdorlarga userbot xabari — navbatni bittalab bo'shatadi ──────
    from bot import notify_worker
    notify_task = asyncio.create_task(notify_worker.loop())

    # ── Qarz harakati: nasiya yoki to'lov bo'lsa mijozga va adminga ─────
    from bot import debt_watch
    debt_watch_task = asyncio.create_task(debt_watch.loop(bot))

    # ── Instagram natijalari: qaysi rolik qancha ko'rilgani ─────────────
    from bot import ig_stats
    ig_stats_task = asyncio.create_task(ig_stats.loop())

    # ── Instagram izohlari va lichkasi: yangisi kelsa adminga ──────────
    from bot import ig_social
    ig_social_task = asyncio.create_task(ig_social.loop_forever())

    # ── Rasm yuklangandan keyingi ish uzilib qolgan bo'lsa — qayta ──────
    from bot import enrich_queue
    enrich_task = asyncio.create_task(enrich_queue.loop())

    # ── YesPOS → Chinor: tovar/qoldiq/narx (har 15 daqiqa, himoyali) ────
    from bot import yespos_sync
    yespos_task = asyncio.create_task(yespos_sync.loop(bot))

    # Kassa kompyuteridan (Tailscale/sftp) kelgan shtrix-kod va mijozlar CSV'si
    from bot import yespos_local_import
    local_import_task = asyncio.create_task(yespos_local_import.loop())

    try:
        # Endi channel_post tinglash kerak emas — ma'lumot SQLite da
        await dp.start_polling(
            bot,
            # `my_chat_member` va `channel_post` — kanal almashganda uning
            # id sini AVTOMATIK bilish uchun (qarang: handlers/kanal_aniq.py).
            # Ularsiz Telegram bu yangilanishlarni umuman yubormaydi va
            # kanal id sini faqat qo'lda qidirishga to'g'ri kelardi.
            allowed_updates=["message", "callback_query",
                             "my_chat_member", "channel_post"]
        )
    finally:
        retention_task.cancel()
        payment_task.cancel()
        chek_bot_task.cancel()
        reels_task.cancel()
        reels_worker_task.cancel()
        reels_pick_task.cancel()
        backup_task.cancel()
        receipts_task.cancel()
        notify_task.cancel()
        debt_watch_task.cancel()
        ig_stats_task.cancel()
        ig_social_task.cancel()
        enrich_task.cancel()
        yespos_task.cancel()
        local_import_task.cancel()
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
