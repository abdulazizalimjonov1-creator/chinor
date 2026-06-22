"""POS ma'lumotlar bazasi — SQLite asosida.

Bu fayl endi faqat YIG'UVCHI: barcha mantiq mavzular bo'yicha alohida
modullarga bo'lingan (database/_base.py, _admins.py, _products.py, ...).
`ChannelDB` ularning hammasidan meros oladi — TASHQI API o'zgarmagan.

Modul xaritasi:
  • _helpers.py     — vaqt, pul formatlash, DB_PATH, SCHEMA
  • _formatters.py  — kanal postlari uchun _fmt_* funksiyalari
  • _base.py        — ulanish, migratsiya, kanal posti, sozlamalar (BaseDB)
  • _admins.py      — adminlar/rollar/ruxsatlar (AdminsMixin)
  • _products.py    — mahsulotlar (ProductsMixin)
  • _clients.py     — mijozlar/qarz/to'lov (ClientsMixin)
  • _sales.py       — kassa sotuvlari (SalesMixin)
  • _orders.py      — buyurtmalar (OrdersMixin)
  • _stats.py       — statistika/hisobotlar (StatsMixin)
"""

# ── Tashqi kod uchun qulaylik (eski importlar buzilmasin) ───────────────────
# Handlerlar `from database.channel_db import db, fmt_usd, ...` qiladi.
from database._helpers import (
    DB_PATH, SCHEMA, now_local, _now,
    fmt_usd, fmt_sum, fmt_money, usd_to_sum, sum_to_usd,
)
from database._base import BaseDB, _PersistentConn
from database._admins import AdminsMixin
from database._products import ProductsMixin
from database._clients import ClientsMixin
from database._sales import SalesMixin
from database._orders import OrdersMixin
from database._stats import StatsMixin
from database._catalog import CatalogMixin
from database._analytics import AnalyticsMixin


class ChannelDB(
    BaseDB,
    AdminsMixin,
    ProductsMixin,
    ClientsMixin,
    SalesMixin,
    OrdersMixin,
    StatsMixin,
    CatalogMixin,
    AnalyticsMixin,
):
    """SQLite + Telegram kanal log.

    Butun mantiq mixin'larga bo'lingan — bu yerda faqat ular birlashtiriladi.
    Yangi metod qo'shmoqchi bo'lsangiz — tegishli modulga (_products.py,
    _clients.py, ...) qo'shing, bu fayl o'zgarmaydi.
    """
    pass


# Yagona global instans — butun loyiha shuni ishlatadi.
db = ChannelDB()
