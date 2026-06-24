from aiogram.fsm.state import State, StatesGroup


class AddProductStates(StatesGroup):
    name            = State()
    description     = State()
    unit            = State()
    sell_price      = State()    # USD da kiritiladi
    wholesale_price = State()    # USD da kiritiladi
    cost_price      = State()    # USD da kiritiladi
    qty             = State()
    image           = State()
    barcode         = State()    # ixtiyoriy — matn yoki shtrix-kod rasmi
    category        = State()    # ixtiyoriy — kategoriya tanlash (inline)
    supplier        = State()    # ixtiyoriy — yetkazib beruvchi tanlash (inline)


class SetUsdRateStates(StatesGroup):
    waiting = State()


class EditProductStates(StatesGroup):
    waiting = State()


class QtyAddStates(StatesGroup):
    enter = State()


class ProductSearchStates(StatesGroup):
    """Admin mahsulotlar ro'yxatida ID/nom/shtrix-kod bilan qidirish."""
    query    = State()
    browsing = State()   # mahsulotlar ro'yxati ochiq — yozsa darrov qidiradi


# ── Kategoriya / Yetkazib beruvchi ───────────────────────────────────────────

class AddCategoryStates(StatesGroup):
    name = State()


class EditCategoryStates(StatesGroup):
    waiting = State()


class AddSupplierStates(StatesGroup):
    name  = State()
    phone = State()
    note  = State()


class EditSupplierStates(StatesGroup):
    waiting = State()   # qaysi maydon — data['edit_field'] da saqlanadi


class SupplierOrderStates(StatesGroup):
    """Yetkazib beruvchidan zakaz ro'yxati tuzish."""
    building = State()
    qty      = State()


class QuickPrixodStates(StatesGroup):
    """Yetkazib beruvchidan tezkor prixod (tovar qo'shish)."""
    picking = State()
    qty     = State()


class QuickRestockStates(StatesGroup):
    """Past qoldiq ro'yxatidan tezkor to'ldirish."""
    qty = State()


class AddClientStates(StatesGroup):
    # tg_id endi so'ralmaydi — mijoz keyin bot orqali kontaktini yuborib
    # o'zini avtomatik avtorizatsiya qiladi (AuthByPhoneStates).
    shop_name   = State()
    phone       = State()
    client_type = State()


class AIChatStates(StatesGroup):
    """AI Analitika — admin erkin savol kiritadigan rejim."""
    question = State()


class ClientSearchStates(StatesGroup):
    """Mijoz tomonidan mahsulot qidirish.
    • searching — matn yoki shtrix-kod rasmini kutish
    • viewing   — natija ko'rsatildi; yana yozsa darrov qidiradi"""
    searching = State()
    viewing   = State()


class ClientConsultStates(StatesGroup):
    """Mijoz AI sotuvchi-konsultant bilan suhbat — savol kutilmoqda."""
    asking = State()


class SetCredentialsStates(StatesGroup):
    """Mini App uchun login/parol yaratish/o'zgartirish oqimi.
    Bir xil oqim ham mijoz, ham admin uchun ishlatiladi."""
    username = State()
    password = State()


class AuthByPhoneStates(StatesGroup):
    """Ro'yxatdan o'tmagan foydalanuvchi /start bosgach — telefon raqamni
    KONTAKT ko'rinishida (request_contact) yuborib o'zini tasdiqlaydi.
    Qo'lda yozilgan matn QABUL QILINMAYDI."""
    waiting_contact = State()


class PaymentStates(StatesGroup):
    amount = State()
    note   = State()


class AddAdminStates(StatesGroup):
    # Hodim endi TELEFON raqami orqali qo'shiladi. Hodim keyin botga kirib
    # o'z kontaktini yuborganda — shu telefon bo'yicha avtomatik admin bo'ladi.
    full_name = State()
    phone     = State()
    tg_id     = State()   # eski oqim (ishlatilmaydi, moslik uchun qoldirilgan)


class OrderStates(StatesGroup):
    browsing = State()
    qty      = State()


class SaleStates(StatesGroup):
    choosing_client    = State()   # sotuv boshida mijoz tanlash
    client_search      = State()   # mijozni nom/telefon bilan qidirish
    scanning           = State()   # mahsulot tanlash (inline tugmalar)
    search_input       = State()   # ID yoki nom orqali qidirish
    variants           = State()   # variant tanlash (inline tugmalar)
    entering_qty       = State()   # miqdor kiritish
    entering_price     = State()   # bir martalik narx (chegirma) kiritish
    payment            = State()   # yakunlash yoki bekor qilish
    entering_discount  = State()   # umumiy summaga chegirma (yumaloqlash)
    entering_paid      = State()   # mijoz qancha to'laganini kiritish
    cart_editing       = State()   # savatni tahrirlash (qator tanlash)
    cart_edit_qty      = State()   # savat qatoriga yangi miqdor kiritish


class PayClientStates(StatesGroup):
    """Mijozdan qarz to'lovini qabul qilish (so'm yoki USD)"""
    amount = State()
    note   = State()
