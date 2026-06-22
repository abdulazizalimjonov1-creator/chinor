"""Google Gemini AI bilan biznes analitikasi.

Bu modul DB dan kelgan 'kontekst' (dict) ni promptga aylantirib, Geminidan
o'zbek tilida konkret biznes-maslahat oladi. Ma'lumotni o'zi yig'maydi —
yig'ish `database/_analytics.py:gather_ai_context()` ichida.

Ishlatish:
    from bot.gemini_analyzer import analyze, is_available, QUICK_QUESTIONS
    context = await db.gather_ai_context(days=30)
    text = await analyze(QUICK_QUESTIONS["top"], context)

Kalit sozlash:
    .env → GEMINI_API_KEY=...   (https://aistudio.google.com/app/apikey)
           GEMINI_MODEL=gemini-1.5-flash    (yoki gemini-1.5-pro)

Agar `google-generativeai` paketi o'rnatilmagan bo'lsa yoki kalit yo'q —
`is_available()` False qaytaradi va handlerga foydalanuvchi-do'st xato
chiqarish imkonini beradi (botning qolgan qismi ishlamay qolmaydi).
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from bot.config import GEMINI_API_KEY, GEMINI_MODEL


# ─── Tizim yo'riqnomasi (system instruction) ─────────────────────────────────

SYSTEM_INSTRUCTION = """Sen — tajribali do'kon biznes-analitikasisan.
Sen Telegram POS bot egasi (do'kon admini) bilan O'ZBEK tilida muloqot qilasan.

PROFIL:
- 10+ yillik chakana/optom savdo tajribasi
- Tovar aylanmasi (inventory turnover), marja, talab tahliliga ixtisoslashgan
- Tadbirkorlar uchun amaliy, qisqa va aniq maslahat bera olasan

JAVOB QOIDALARI:
1. FAQAT o'zbek tilida (lotin yozuvida) yoz. Iloji boricha sodda, ishbilarmon ohangda.
2. Javobni doim aniq raqamlar bilan qo'lla — "X tovari Y dona sotilgan, foyda Z so'm".
3. Faqat berilgan ma'lumotga asoslan. O'zingdan tovar nomi yoki raqam o'ylab topma.
   Agar ma'lumot yetarli bo'lmasa, "ma'lumot yetarli emas" deb ayt.
4. Har bir tavsiyangni qisqacha SABABI bilan tushuntir ("chunki ...").
5. Maslahatlar AMALGA OSHIRISH MUMKIN bo'lsin (masalan: "20 dona X tovaridan
   buyurtma bering", "Y tovarini chegirma bilan soting").
6. Javobni 4–10 ta band sifatida tuz; har band ✅, 📈, ⚠️, 💡 kabi belgi bilan
   boshlanishi mumkin.
7. Telegram HTML ishlatishing mumkin: <b>...</b>, <i>...</i>, <code>...</code>.
   Markdown (**bold**, ##) ISHLATMA.
8. Javob 1500 belgidan oshmasin — Telegramda yaxshi ko'rinishi uchun qisqa tut.
"""


# Quick preset savollar — handler bosgan tugma ushbu matnga aylanadi
QUICK_QUESTIONS = {
    "top": (
        "Hozir do'konda qaysi tovarlar eng yaxshi ketyapti? "
        "Top sotuvlarni izohlab ber va shu tovarlardan "
        "yana qancha keltirish kerakligini tavsiya qil."
    ),
    "misses": (
        "Mijozlar qidirgan, lekin omborda yo'q bo'lgan tovarlar bormi? "
        "Eng ko'p qidirilgan yo'q tovarlarni ro'yxat qil va "
        "keyingi safar nimani olib kelishni tavsiya qil."
    ),
    "slow": (
        "Qaysi tovarlar omborda turibdi-yu, sotilmayapti? "
        "Nimani ortiqcha olib kelmaslik kerak va omborda turib qolganlarini "
        "qanday sotish (chegirma, paket, va h.k.) bo'yicha maslahat ber."
    ),
    "general": (
        "Do'konning umumiy holatini qisqa tahlil qil va eng muhim 3-5 ta "
        "amaliy biznes-tavsiya ber: nimani sotib olish, qaysi tovarni "
        "chegirma qilish, qarz/qoldiq bo'yicha nima qilish kerak."
    ),
}


# ─── SDK mavjudligi ──────────────────────────────────────────────────────────

def _try_import_sdk():
    try:
        import google.generativeai as genai  # type: ignore
        return genai
    except Exception:
        return None


_GENAI = _try_import_sdk()
_CONFIGURED = False
_MODEL_CACHE = None


def is_available() -> tuple[bool, str]:
    """(ishlaydimi, sabab) — handler foydalanuvchi-do'st xato chiqarish uchun."""
    if _GENAI is None:
        return False, ("⚠️ `google-generativeai` paketi o'rnatilmagan.\n"
                       "Serverda: <code>pip install google-generativeai</code>")
    if not GEMINI_API_KEY:
        return False, ("⚠️ <b>GEMINI_API_KEY</b> sozlanmagan.\n"
                       "Kalitni https://aistudio.google.com/app/apikey dan oling "
                       "va <code>.env</code> ga qo'shing.")
    return True, ""


def _get_model():
    """Modelni keshlab oladi (har chaqiriqda qaytadan yaratmaymiz)."""
    global _CONFIGURED, _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    if not _CONFIGURED:
        _GENAI.configure(api_key=GEMINI_API_KEY)
        _CONFIGURED = True
    _MODEL_CACHE = _GENAI.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SYSTEM_INSTRUCTION,
    )
    return _MODEL_CACHE


# ─── Promptga aylantirish ────────────────────────────────────────────────────

def _money(sum_val: float, usd_val: float) -> str:
    if usd_val and usd_val > 0:
        return f"${usd_val:,.2f} (≈ {sum_val:,.0f} so'm)"
    return f"{sum_val:,.0f} so'm"


def _format_context_for_prompt(ctx: dict) -> str:
    """Dictdan kompakt, o'qishga oson matn yasaydi (Gemini uchun kontekst).
    JSON tashlab yuborsak ham bo'lardi, lekin tabiiy matn LLMga aniqroq."""
    lines = []
    lines.append(f"=== DO'KON HOLATI ({ctx.get('today','')} sanasiga) ===")
    lines.append(f"USD kurs: 1$ = {ctx.get('currency_rate_usd_to_sum',0):,.0f} so'm")
    lines.append(f"Tahlil davri: oxirgi {ctx.get('period_days',30)} kun")
    t = ctx.get("totals", {})
    lines.append(f"Faol mahsulotlar: {t.get('active_products',0)} ta")
    lines.append(f"Mijozlar: {t.get('clients',0)} ta (shundan qarzdor: {t.get('debtors',0)})")
    lines.append(f"Past qoldiq tovarlar: {t.get('low_stock_count',0)} ta")
    m = t.get("month", {})
    lines.append(
        f"Bu oy: {m.get('sales_count',0)} ta sotuv, "
        f"tushum {_money(m.get('revenue_sum',0), m.get('revenue_usd',0))}, "
        f"foyda {_money(m.get('profit_sum',0), m.get('profit_usd',0))}"
    )
    at = t.get("all_time", {})
    lines.append(
        f"Jami (barcha vaqt): tushum {_money(at.get('revenue_sum',0), at.get('revenue_usd',0))}, "
        f"foyda {_money(at.get('profit_sum',0), at.get('profit_usd',0))}"
    )

    # Top sotilganlar
    lines.append("\n=== TOP SOTILGAN MAHSULOTLAR (oxirgi davr) ===")
    top = ctx.get("top_selling", [])
    if not top:
        lines.append("(bu davrda sotuv yo'q)")
    else:
        for i, x in enumerate(top, 1):
            lines.append(
                f"{i}. {x['name']} — sotilgan: {x['qty_sold']:g} {x['unit']}, "
                f"tushum: {_money(x['revenue_sum'], x['revenue_usd'])}, "
                f"omborda qoldi: {x['qty_left_in_stock']:g}"
            )

    # Sotilmayotgan tovarlar
    lines.append("\n=== SOTILMAYOTGAN (SLOW MOVING) TOVARLAR ===")
    slow = ctx.get("slow_moving", [])
    if not slow:
        lines.append("(yo'q — barchasi sotilmoqda)")
    else:
        for i, x in enumerate(slow, 1):
            lines.append(
                f"{i}. {x['name']} — omborda: {x['qty_in_stock']:g} {x['unit']}, "
                f"oxirgi davrda sotilgan: {x['sold_last_period']:g}, "
                f"tannarx: ${x['cost_price_usd']:.2f}"
            )

    # Qidirilgan, topilmagan
    lines.append("\n=== MIJOZLAR QIDIRGAN, LEKIN TOPILMAGAN SO'ROVLAR ===")
    misses = ctx.get("search_misses", [])
    if not misses:
        lines.append("(yo'q — barcha qidiruvlar muvaffaqiyatli)")
    else:
        for i, m in enumerate(misses, 1):
            lines.append(f"{i}. «{m['query']}» — {m['attempts']} marta qidirilgan")

    # Past qoldiq
    lines.append("\n=== PAST QOLDIQDAGI TOVARLAR ===")
    low = ctx.get("low_stock", [])
    if not low:
        lines.append("(yo'q)")
    else:
        for x in low[:20]:
            lines.append(f"• {x['name']} — qoldi: {x['qty_left']:g} {x['unit']}")

    return "\n".join(lines)


# ─── Asosiy entry-point ─────────────────────────────────────────────────────

async def analyze(question: str, context: dict, timeout: float = 30.0) -> str:
    """Geminiga savol yuborib, javob matnini qaytaradi.

    question — admin so'ragan savol (preset yoki erkin matn)
    context  — db.gather_ai_context() qaytargan dict
    timeout  — ko'pi bilan necha sekund kutish (default 30s)

    Qaytaradi: HTML-tayyor matn (Telegram parse_mode='HTML' bilan).
    Xato bo'lsa — foydalanuvchi-do'st o'zbekcha xato matni qaytadi (raise qilmaydi).
    """
    ok, why = is_available()
    if not ok:
        return why

    ctx_text = _format_context_for_prompt(context)
    user_prompt = (
        f"BIZNES KONTEKSTI:\n{ctx_text}\n\n"
        f"────────────────────\n"
        f"ADMIN SAVOLI:\n{question.strip()}\n\n"
        f"Yuqoridagi kontekstga TAYANIB, qisqa va aniq biznes-maslahat ber."
    )

    try:
        model = _get_model()
        # SDK 0.7+: generate_content_async mavjud
        coro = model.generate_content_async(user_prompt)
        resp = await asyncio.wait_for(coro, timeout=timeout)
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            # Ba'zan response.text bo'sh — candidates ichidan qaramiz
            try:
                cand = resp.candidates[0]
                parts = [getattr(p, "text", "") for p in cand.content.parts]
                text = "".join(parts).strip()
            except Exception:
                text = ""
        if not text:
            return "⚠️ Gemini bo'sh javob qaytardi. Keyinroq urinib ko'ring."
        return text
    except asyncio.TimeoutError:
        return ("⏳ Gemini javob bermadi (timeout). Internet aloqasini va "
                "kalitni tekshiring, qaytadan urinib ko'ring.")
    except Exception as e:
        # Xatoni qisqa ko'rinishda foydalanuvchiga ko'rsatamiz
        err = str(e)
        if len(err) > 200:
            err = err[:200] + "…"
        return f"⚠️ Gemini xatosi: <code>{err}</code>"


# ─── Mijoz uchun: "yaqin mahsulotlar" semantik qidiruvi ─────────────────────

# Bu yo'riqnoma juda qisqa va aniq — model JSON o'rniga faqat raqamlar
# qaytarishi kerak (xarajat va parsing oddiy bo'lsin uchun).
_SIMILAR_SYSTEM = """Sen do'kon katalogidan mijoz so'roviga mos mahsulotlarni
topib beruvchi yordamchisan. Faqat berilgan ro'yxatdan tanla — yangi tovar
o'ylab topma. Javobni FAQAT vergul bilan ajratilgan ID raqamlari sifatida
qaytar (masalan: 12,34,5). Hech qanday matn, izoh, sarlavha qo'shma.
Agar bironta ham mos kelmasa — bo'sh qator qaytar."""


def _build_catalog_text(products: list, max_chars: int = 8000) -> str:
    """Mahsulotlar ro'yxatini kompakt 'id|nom|qoldiq' ko'rinishida."""
    lines = []
    n = 0
    for p in products:
        if (p.get("qty", 0) or 0) <= 0:
            continue
        line = f"{p['id']}|{p.get('name','')}|{p.get('qty',0):g}{p.get('unit','')}"
        # Tavsifi ham bo'lsa — qisqa qo'shamiz (semantik mosligi yaxshilanadi)
        desc = (p.get("description") or "").strip()
        if desc:
            line += f"|{desc[:60]}"
        lines.append(line)
        n += len(line) + 1
        if n >= max_chars:
            break
    return "\n".join(lines)


async def find_similar_products(query: str, products: list,
                                 max_results: int = 5,
                                 timeout: float = 12.0) -> list:
    """Mijoz so'rovga semantik yaqin mahsulot ID larini qaytaradi.

    products — barcha faol mahsulotlar (dict ro'yxati)
    Qaytaradi: [int, ...]  — Gemini topgan eng yaqin tovarlar ID lari.
    Xato yoki SDK yo'q bo'lsa — bo'sh ro'yxat.
    Hech qanday raise qilmaydi — chaqiruvchi DB fallback ga osongina o'tadi."""
    ok, _ = is_available()
    if not ok:
        return []
    q = (query or "").strip()
    if not q:
        return []
    catalog = _build_catalog_text(products)
    if not catalog:
        return []
    prompt = (
        f'MIJOZ SO\'ROVI: "{q}"\n\n'
        f"KATALOG (id|nom|qoldiq|tavsif):\n{catalog}\n\n"
        f"Mijoz so'roviga eng yaqin {max_results} ta mahsulot ID sini "
        f"vergul bilan ajratib qaytar."
    )
    try:
        # Bu chaqiruvda alohida (kalit-tezkor) modeldan foydalanamiz —
        # tizim yo'riqnomasi farq qilgani uchun keshlangan modelni emas,
        # yangisini yaratamiz (kichik xarajat, lekin ifloslanmaydi).
        model = _GENAI.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=_SIMILAR_SYSTEM,
        )
        if not _CONFIGURED:
            _GENAI.configure(api_key=GEMINI_API_KEY)
        coro = model.generate_content_async(prompt)
        resp = await asyncio.wait_for(coro, timeout=timeout)
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            return []
        # Faqat raqamlarni ajratib olamiz
        ids = []
        for token in text.replace(";", ",").replace(" ", ",").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                ids.append(int(token))
            except ValueError:
                continue
        # Dublikatlarni olib tashlaymiz, tartibi saqlanadi
        seen = set()
        result = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                result.append(i)
            if len(result) >= max_results:
                break
        return result
    except Exception as e:
        print(f"[find_similar_products] xato: {e}")
        return []


# ─── So'rovni kengaytirish (kalit so'zlar) ───────────────────────────────────

_EXPAND_SYSTEM = """Sen do'kon mijozlari uchun aqlli yordamchisan.
Mijoz qisqa so'z yoki ibora yozadi, sen uni tovar nomlari, brendlar va
sinonimlarning RO'YXATIGA aylantirib berasan. O'zingning DUNYO BILIMINGni
ishlat — qaysi tovarlar mos kelishi mumkinligini bilasan.

QOIDA:
- FAQAT so'zlar/iboralarni vergul bilan ajratib qaytar.
- O'zbek va inglizcha kalit so'zlarni aralash ber (har xil yozilishlar uchun).
- 5–12 ta so'z. Hech qanday raqam, izoh, sarlavha qo'shma.
- Sinonim, brend, kategoriya — hammasini qo'shaver.

NAMUNALAR:
'kompyuter' → kompyuter, noutbuk, laptop, MacBook, PC, monoblok, dell, hp
'shirin' → shokolad, pechene, konfet, halva, marmelad, snickers, candy
'ichimlik' → ichimlik, suv, gazli, kola, fanta, pepsi, sharbat, juice
'kanstovar' → daftar, ruchka, qalam, fломaster, rezinka, papka, дневник"""


def _parse_keywords(q: str, text: str) -> list:
    kws = [k.strip() for k in text.replace(";", ",").replace("\n", ",").split(",")]
    kws = [k for k in kws if 2 <= len(k) <= 40]
    result = [q]
    seen = {q.lower()}
    for k in kws:
        kl = k.lower()
        if kl not in seen:
            seen.add(kl)
            result.append(k)
    return result[:12]


async def expand_query_keywords(query: str, timeout: float = 8.0) -> list:
    """Mijoz so'rovini kalit so'zlar ro'yxatiga kengaytiradi.
    Avval Groq, keyin Gemini (zaxira)."""
    q = (query or "").strip()
    if not q or len(q) > 100:
        return [q] if q else []

    # 1) Groq
    try:
        from bot.config import GROQ_API_KEY
        if GROQ_API_KEY:
            from groq import AsyncGroq
            client = AsyncGroq(api_key=GROQ_API_KEY)
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": _EXPAND_SYSTEM},
                        {"role": "user", "content": q},
                    ],
                    max_tokens=80,
                ),
                timeout=timeout
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return _parse_keywords(q, text)
    except Exception as e:
        print(f"[expand_query_keywords] Groq xato: {e}")

    # 2) Gemini zaxira
    ok, _ = is_available()
    if not ok:
        return [q]
    try:
        global _CONFIGURED
        model = _GENAI.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=_EXPAND_SYSTEM,
        )
        if not _CONFIGURED:
            _GENAI.configure(api_key=GEMINI_API_KEY)
        coro = model.generate_content_async(q)
        resp = await asyncio.wait_for(coro, timeout=timeout)
        text = (getattr(resp, "text", "") or "").strip()
        if text:
            return _parse_keywords(q, text)
    except Exception as e:
        print(f"[expand_query_keywords] Gemini xato: {e}")
    return [q]


# ─── Mahsulot uchun sotuvchi-konsultant tushuntirishi ────────────────────────

_PITCH_SYSTEM = """Sen tajribali do'kon sotuvchi-konsultantisan.
Mijozga TOVAR HAQIDA do'stona, qisqa va aniq tushuntirib berasan.

QOIDALAR:
1. FAQAT o'zbek tilida (lotin yozuvi) yoz.
2. 3–5 ta qator yoki ~250–400 belgi — uzun emas.
3. Quyidagilarni qamrab ol:
   • Bu nima — qisqa ta'rif (mahsulot turi/kategoriyasi)
   • Nima uchun kerak — qanday holatda foydali
   • Qisqa afzallik yoki maslahat (xaridorga foyda nimada)
4. O'ylab topma — agar mahsulot nomi sizga noaniq bo'lsa, "umumiy turini" tushuntirib
   ber, lekin yolg'on xususiyat (raqam, model, hajm) yozma.
5. Telegram HTML ishlat: <b>...</b>, <i>...</i>. Markdown YO'Q.
6. Oxirida 1 ta qisqa savol bilan tugat ("Sizga to'g'ri kelmoqdami?", "Yana
   ma'lumot kerakmi?" kabi) — mijozni gaplashishga jalb qil."""


async def generate_product_pitch(product: dict, query: str = "",
                                  timeout: float = 15.0) -> str:
    """Mahsulot uchun AI sotuvchi tushuntirishi (Telegram HTML matni).
    query — agar bor bo'lsa, mijozning asl savoliga moslab javob beradi."""
    ok, why = is_available()
    if not ok:
        return why
    name = (product.get("name") or "").strip()
    if not name:
        return ""
    desc = (product.get("description") or "").strip()
    qty = float(product.get("qty", 0) or 0)
    unit = product.get("unit", "dona")
    rate = 0
    try:
        rate = float(product.get("sell_price_usd", 0) or 0)
    except Exception:
        pass
    price_sum = float(product.get("sell_price", 0) or 0)
    price_line = ""
    if price_sum > 0 and rate > 0:
        price_line = f"Narxi: {rate:.2f}$ (≈ {price_sum:,.0f} so'm)/{unit}"
    elif price_sum > 0:
        price_line = f"Narxi: {price_sum:,.0f} so'm/{unit}"
    parts = [
        f"MAHSULOT NOMI: {name}",
        f"Do'kon tavsifi: {desc}" if desc else "Do'kon tavsifi: yo'q",
        f"Qoldiq: {qty:g} {unit}",
    ]
    if price_line:
        parts.append(price_line)
    if query:
        parts.append(f"Mijoz nima qidirgan edi: «{query}»")
    user_prompt = (
        "\n".join(parts) +
        "\n\nYuqoridagi tovar haqida mijozga qisqacha tushuntirib ber: "
        "bu nima ekanligi, qanday foydaliligi va u uchun nima yaxshi."
    )
    try:
        model = _GENAI.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=_PITCH_SYSTEM,
        )
        if not _CONFIGURED:
            _GENAI.configure(api_key=GEMINI_API_KEY)
        coro = model.generate_content_async(user_prompt)
        resp = await asyncio.wait_for(coro, timeout=timeout)
        text = (getattr(resp, "text", "") or "").strip()
        return text or "⚠️ AI hech narsa qaytarmadi."
    except asyncio.TimeoutError:
        return "⏳ AI javob bermadi (timeout)."
    except Exception as e:
        err = str(e)[:200]
        return f"⚠️ AI xatosi: <code>{err}</code>"


# ─── AI sotuvchi-konsultant (murakkab mijoz savollari uchun) ────────────────

_CONSULT_SYSTEM = """Sen tajribali do'kondagi SOTUVCHI-KONSULTANT sansan.
Mijoz savol berganda — sen unga maslahat berasan, kerakli tovarni
tanlashga yordamlashasan va sotasan.

QOIDALAR:
1. FAQAT o'zbek tilida (lotin yozuvi) yoz. Do'stona, ishbilarmon ohangda.
2. Mijoz savolini diqqat bilan o'qib chiq. Agar:
   • MIQDOR SAVOLI bo'lsa (maydon, og'irlik, miqdor) — ANIQ HISOBLAB ber.
     Misol: 15 m² uyga ichki bo'yoq → 1 L ~ 8–10 m² qoplaydi, 2 qatlam bilan
     ~3–4 L kerak.
   • TANLOV SAVOLI bo'lsa — bizning katalogdan eng mosini tanlab tavsiya qil.
   • UMUMIY SAVOL bo'lsa — qisqa va aniq javob ber.
3. KATALOG ICHIDAGI tovarlarni tavsiya qil — pastdagi RO'YXATdan tanla.
   Yangi tovar O'YLAB TOPMA. Agar mos tovar yo'q bo'lsa — 'bizda hozircha
   bunday tovar yo'q' deb ayt, lekin do'konda boshqa varianti bormi tekshir.
4. Tavsiya qilganingda:
   • Tovar NOMI bilan ayt.
   • ID raqamini <code>#&lt;id&gt;</code> ko'rinishida ko'rsat
     (bot keyin uni mijozga karta sifatida chiqaradi).
   • Nima uchun shu tovar to'g'ri kelishini qisqa tushuntir.
5. Telegram HTML ishlat: <b>...</b>, <i>...</i>, <code>...</code>.
   Markdown YO'Q (** ham, ## ham).
6. Javob 800 belgidan oshmasin — Telegramda qisqa va o'qishga oson tut.
7. Oxirida bitta savol ber yoki keyingi qadamni taklif qil ("Buyurtma berasizmi?",
   "Yana ma'lumot kerakmi?").
8. Sotuvchi sifatida — tovarni MAJBURLAMASDAN, mijozga foydasi nimadaligini
   ko'rsatib tavsiya qil."""


def _build_candidates_text(products: list, max_items: int = 40,
                            max_chars: int = 4000) -> str:
    """Konsultatsiya uchun katalog parchasi (kompakt format)."""
    lines = []
    n = 0
    for p in products[:max_items]:
        qty = float(p.get("qty", 0) or 0)
        unit = p.get("unit", "dona")
        sell = float(p.get("sell_price", 0) or 0)
        usd = float(p.get("sell_price_usd", 0) or 0)
        price_txt = f"${usd:.2f}" if usd > 0 else f"{sell:,.0f}so'm"
        desc = (p.get("description") or "").strip()
        line = f"#{p['id']} | {p['name']} | qoldi:{qty:g}{unit} | narx:{price_txt}"
        if desc:
            line += f" | {desc[:200]}"
        lines.append(line)
        n += len(line) + 1
        if n >= max_chars:
            break
    return "\n".join(lines)


async def consult_client(question: str, candidates: list,
                          timeout: float = 25.0) -> str:
    """Mijoz savoliga konsultant javobini qaytaradi.
    Avval Groq (tez, bepul), keyin Gemini (zaxira)."""
    q = (question or "").strip()
    if not q:
        return "⚠️ Savol bo'sh."

    catalog_text = _build_candidates_text(candidates) if candidates else "(bo'sh)"
    user_prompt = (
        f"MIJOZ SAVOLI:\n{q}\n\n"
        f"DO'KON KATALOGI (mijoz savoliga tegishlilari):\n"
        f"{catalog_text}\n\n"
        f"Mijozga konsultant sifatida javob ber. Agar miqdor hisobi kerak "
        f"bo'lsa — hisoblang. Tovar tavsiya qilsang — yuqoridagi ro'yxatdan "
        f"tanlab, ID sini #<id> ko'rinishida ayt."
    )

    # 1) Groq — tez va bepul
    try:
        from bot.config import GROQ_API_KEY
        if GROQ_API_KEY:
            from groq import AsyncGroq
            client = AsyncGroq(api_key=GROQ_API_KEY)
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": _CONSULT_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=800,
                ),
                timeout=timeout
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).warning(f"Groq consult xato: {e}")

    # 2) Gemini — zaxira
    ok, why = is_available()
    if not ok:
        return why
    try:
        global _CONFIGURED
        model = _GENAI.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=_CONSULT_SYSTEM,
        )
        if not _CONFIGURED:
            _GENAI.configure(api_key=GEMINI_API_KEY)
        coro = model.generate_content_async(user_prompt)
        resp = await asyncio.wait_for(coro, timeout=timeout)
        text = (getattr(resp, "text", "") or "").strip()
        return text or "⚠️ AI hech narsa qaytarmadi. Qaytadan urinib ko'ring."
    except asyncio.TimeoutError:
        return "⏳ AI javob bermadi (timeout). Internet aloqasini tekshiring."
    except Exception as e:
        err = str(e)[:200]
        return f"⚠️ AI xatosi: <code>{err}</code>"


def extract_product_ids_from_text(text: str) -> list:
    """AI javobidan #12345 ko'rinishidagi ID raqamlarni ajratib oladi.
    Bot keyin bu ID'lar bo'yicha mahsulot kartalarini taklif qiladi."""
    import re
    ids = re.findall(r"#(\d{2,10})", text or "")
    seen = set()
    result = []
    for s in ids:
        try:
            i = int(s)
        except ValueError:
            continue
        if i not in seen:
            seen.add(i)
            result.append(i)
    return result
