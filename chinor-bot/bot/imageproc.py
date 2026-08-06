"""Mahsulot rasmini «do'kon ko'rinishi»ga keltirish: fonni kesish + brend fon.

MUAMMO: xodimlar tovarni telefonda qo'lda ushlab, do'kon peshtaxtasi fonida
suratga oladi. Katalogda ular rang-barang, qiyshiq va chalg'ituvchi ko'rinadi.

YECHIM: tovarning o'zi kesib olinadi va bir xil OCH YASHIL kvadrat fonga
qo'yiladi, burchagiga Chinor logotipi bosiladi. Katalog bir butun ko'rinadi.
ASL rasm o'chirilmaydi — u ikkinchi rasm sifatida saqlanadi (mijoz "asl
rasm"ni ko'ra oladi, tovar haqiqatda qanday ekanini tekshirish uchun).

FONNI KESISH — **u2netp** neyron modeli (~4.6 MB) `onnxruntime` orqali.
Model faylini bir marta o'zi yuklab oladi (DATA_DIR/.u2net/).

Nima uchun `rembg` paketi EMAS: u aynan shu modelni ishlatadi, lekin ustiga
numba + scikit-image + pymatting tortadi va 1 GB li serverda bot bilan birga
xotiraga sig'maydi (OOM — 2026-08-05 da VPS'da shundoq bo'ldi). Shuning uchun
modelning o'zi to'g'ridan-to'g'ri chaqiriladi: natija bir xil, xotira ~4 barobar
kam. `rembg` o'rnatilgan bo'lsa ham talab qilinmaydi.

    pip install onnxruntime         # ixtiyoriy, lekin tavsiya etiladi

`onnxruntime` yo'q bo'lsa modul YIQILMAYDI — shunchaki fon kesilmaydi, rasm
brend fonga to'liq sig'diriladi (letterbox). Katalog baribir bir xil ko'rinadi.

Ishlatish:
    from bot import imageproc
    out, info = imageproc.brand(raw_bytes)      # -> (jpeg bytes, dict)
    if info["ok"]: ...                          # info["cut"] — fon kesildimi

Bloklovchi (CPU) funksiya: aiohttp ichida `run_in_executor` bilan chaqiring.
"""

from __future__ import annotations

import io
import logging
import os
import threading

logger = logging.getLogger(__name__)

# ─── Ko'rinish sozlamalari ─────────────────────────────────────────────────
CANVAS = 1200                      # kvadrat tomoni (px)
BG_TOP = (243, 251, 244)           # och yashil — yuqori
BG_BOTTOM = (205, 234, 212)        # och yashil — past (yumshoq gradient)
OBJECT_RATIO = 0.78                # tovar kanvasning necha qismini egallaydi
LOGO_RATIO = 0.155                 # logotip kanvasga nisbatan
LOGO_MARGIN = 0.035                # burchakdan chekinish
JPEG_QUALITY = 88

# Model: u2netp — kichik (4.6 MB) va tez, 1 GB li serverga ham sig'adi.
# Sifat yetarli bo'lmasa .env da: CUTOUT_MODEL=u2net (176 MB, aniqroq).
CUTOUT_MODEL = os.getenv("CUTOUT_MODEL", "u2netp")
MODEL_URL = ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
             "{}.onnx")
MODEL_INPUT = 320                  # standart; aniq o'lcham modeldan o'qiladi

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets")
LOGO_PATH = os.getenv("BRAND_LOGO", os.path.join(_ASSETS, "logo.png"))

# Model fayli QAYERDA turadi. Serverda bot `nologin` foydalanuvchi ostida
# ishlaydi va uning HOME'i yozib bo'lmaydigan bo'lishi mumkin — shuning uchun
# ma'lumotlar papkasiga qo'yamiz (bir marta yuklanadi, keyin shu yerdan).
_DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data"))
MODEL_DIR = os.environ.get("U2NET_HOME") or os.path.join(_DATA_DIR, ".u2net")

_session = None                    # onnxruntime sessiyasi (model bir marta)
_session_lock = threading.Lock()
_session_failed = False
_logo_cache = None


# ─── Fonni kesish ──────────────────────────────────────────────────────────

def has_cutout() -> bool:
    """Fonni kesish mumkinmi (onnxruntime bormi) — modelni yuklamasdan."""
    try:
        import onnxruntime  # noqa: F401
        import numpy        # noqa: F401
        return True
    except Exception:
        return False


def _model_path() -> str:
    """Model faylini qaytaradi, yo'q bo'lsa bir marta yuklab oladi."""
    path = os.path.join(MODEL_DIR, f"{CUTOUT_MODEL}.onnx")
    if os.path.isfile(path) and os.path.getsize(path) > 1_000_000:
        return path
    import urllib.request
    os.makedirs(MODEL_DIR, exist_ok=True)
    url = MODEL_URL.format(CUTOUT_MODEL)
    logger.info("🖼 Model yuklab olinmoqda: %s", url)
    tmp = path + ".part"
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, path)
    logger.info("🖼 Model saqlandi: %s (%.1f MB)",
                path, os.path.getsize(path) / 1048576)
    return path


def _get_session():
    """onnxruntime sessiyasi — bir marta yuklanadi, keyin qayta ishlatiladi.

    Yiqilsa boshqa urinilmaydi (`_session_failed`) — har rasmda qayta-qayta
    kutib o'tirmaslik uchun.
    """
    global _session, _session_failed
    if _session is not None or _session_failed:
        return _session
    with _session_lock:
        if _session is not None or _session_failed:
            return _session
        try:
            import onnxruntime as ort
            so = ort.SessionOptions()
            # Bitta yadroli serverda ko'p oqim foyda bermaydi, xotira esa
            # oshadi — shuning uchun cheklaymiz.
            so.intra_op_num_threads = int(os.getenv("CUTOUT_THREADS", "1"))
            so.inter_op_num_threads = 1
            so.log_severity_level = 3
            # Xotira "arena"si o'chirilgan: u tezlik uchun katta blok ushlab
            # turadi va uni qaytarmaydi — 1 GB li serverda bot bilan birga
            # sig'maydi. O'chirilganda sekinlik sezilmaydi (rasm kamdan-kam).
            so.enable_cpu_mem_arena = False
            _session = ort.InferenceSession(
                _model_path(), sess_options=so,
                providers=["CPUExecutionProvider"])
            logger.info("🖼 Fon kesish modeli tayyor: %s", CUTOUT_MODEL)
        except Exception as e:
            _session_failed = True
            logger.warning("🖼 Fon kesish ishga tushmadi (fon kesilmaydi): %s", e)
    return _session


def _clean_alpha(im):
    """Alfa maskani tozalaydi — chekkadagi «tuman»ni olib tashlaydi.

    Kichik model (u2netp) chegarada yarim-shaffof piksellar qoldiradi; ular
    och yashil fonda oq xira dog' bo'lib ko'rinadi. Shuning uchun past alfa
    nolga, yuqori alfa to'liq qorong'iga tortiladi, orada esa yumshoq o'tish
    qoldiriladi (aks holda chekka arradek tishli bo'ladi).
    """
    from PIL import ImageFilter
    lo, hi = 60, 190

    def curve(v):
        if v <= lo:
            return 0
        if v >= hi:
            return 255
        return int((v - lo) * 255 / (hi - lo))

    a = im.getchannel("A").point(curve)
    a = a.filter(ImageFilter.GaussianBlur(0.6))     # chekkani yumshatamiz
    im.putalpha(a)
    return im


def _input_size(sess) -> int:
    """Model qanday o'lchamdagi kirishni kutadi (u2net=320, isnet=1024)."""
    try:
        shape = sess.get_inputs()[0].shape
        n = shape[-1]
        if isinstance(n, int) and n > 32:
            return n
    except Exception:
        pass
    return MODEL_INPUT


def _mask(im, sess):
    """Rasm → tovar maskasi (L rejim, asl o'lchamda).

    u2net/isnet oilasining standart tayyorlash tartibi: model kutgan
    o'lchamga keltirish, ImageNet o'rtacha/og'ish bo'yicha normallashtirish,
    NCHW. O'lcham modeldan o'qiladi — turli modellar turlicha kutadi.
    """
    import numpy as np
    from PIL import Image

    size = _input_size(sess)
    small = im.convert("RGB").resize((size, size), Image.LANCZOS)
    ary = np.array(small, dtype=np.float32)
    ary /= max(float(ary.max()), 1.0)
    # Har model o'z normallashtirishini kutadi: isnet oilasi 0.5/1.0,
    # u2net oilasi esa ImageNet o'rtacha/og'ishi. Aralashtirilsa maska
    # bo'sh chiqadi (isnet 3% berdi — 2026-08-06 da shunday bo'ldi).
    if CUTOUT_MODEL.startswith("isnet"):
        mean = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        std = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    else:
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    ary = (ary - mean) / std
    tensor = np.expand_dims(ary.transpose(2, 0, 1), 0).astype(np.float32)

    pred = sess.run(None, {sess.get_inputs()[0].name: tensor})[0][:, 0, :, :]
    mi, ma = float(pred.min()), float(pred.max())
    pred = (pred - mi) / (ma - mi) if ma > mi else pred * 0
    mask = Image.fromarray((np.squeeze(pred) * 255).astype("uint8"), mode="L")
    return mask.resize(im.size, Image.LANCZOS)


def _tidy_mask(a):
    """Maskadagi mayda "chang" va teshiklarni tozalaydi.

    Model ba'zan tovardan uzoqda bir-ikki dog' qoldiradi yoki ichida teshik
    ochadi. Morfologik yopish (MaxFilter → MinFilter) teshikni yopadi,
    ochish (MinFilter → MaxFilter) esa changni oladi. Bu chekkani deyarli
    o'zgartirmaydi, lekin kollajni ancha toza qiladi.
    """
    from PIL import ImageFilter
    a = a.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    a = a.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return a


def _mask_sane(a) -> bool:
    """Maska ishonchlimi: tovar juda kichik ham, deyarli butun kadr ham emas.

    Ishonchsiz maska bilan sahnaga qo'yish yomon natija beradi (yarim tovar
    yoki butun fon) — bunday holda kesishdan voz kechgan ma'qul.
    """
    try:
        import numpy as np
        arr = np.array(a)
        share = float((arr > 128).mean())
        return 0.02 <= share <= 0.96
    except Exception:
        return True


def cutout(im):
    """PIL Image → foni kesilgan RGBA. Kesib bo'lmasa None."""
    sess = _get_session()
    if sess is None:
        return None
    try:
        a = _tidy_mask(_mask(im, sess))
        if not _mask_sane(a):
            logger.warning("🖼 Maska ishonchsiz — fon kesilmadi")
            return None
        out = im.convert("RGBA")
        out.putalpha(a)
        out = _clean_alpha(out)
        # Alfa butunlay bo'sh bo'lsa (model hech narsa topmadi) — foydasi yo'q.
        if out.getchannel("A").getbbox() is None:
            return None
        return out
    except Exception as e:
        logger.warning("🖼 Fon kesilmadi: %s", e)
        return None


# ─── Brend fon ─────────────────────────────────────────────────────────────

def _background(size: int):
    """Och yashil fon: vertikal gradient + markazda «yorug'lik» (spotlight).

    Faqat tekis gradient quruq ko'rinardi — tovar fonga singib ketardi.
    Markazdagi yumshoq oq yorug'lik tovarni ajratib turadi va rasm
    do'kon vitrinasidek jonli chiqadi.
    """
    from PIL import Image, ImageDraw, ImageFilter
    bg = Image.new("RGB", (1, size))
    px = bg.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
    bg = bg.resize((size, size), Image.BILINEAR)

    # Markaziy yorug'lik — kichik maskani kattalashtirib chizamiz (tez).
    small = size // 6
    glow = Image.new("L", (small, small), 0)
    d = ImageDraw.Draw(glow)
    d.ellipse([small * 0.14, small * 0.06, small * 0.86, small * 0.74], fill=125)
    glow = glow.filter(ImageFilter.GaussianBlur(small // 7)).resize(
        (size, size), Image.BILINEAR)
    bg.paste(Image.new("RGB", (size, size), (255, 255, 255)), (0, 0), glow)
    return bg


def _podium(canvas: int, width: int, y: int):
    """Tovar tagidagi ellips «taglik» — u havoda osilib turmasin."""
    from PIL import Image, ImageDraw, ImageFilter
    lay = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    w = int(width * 0.92)
    h = int(canvas * 0.045)
    d.ellipse([(canvas - w) // 2, y - h // 2, (canvas + w) // 2, y + h // 2],
              fill=(96, 150, 115, 120))
    return lay.filter(ImageFilter.GaussianBlur(canvas // 90))


def _font(size: int, bold: bool = True):
    """Bandlangan shrift (assets/fonts) → tizim shrifti → oxirgi chora."""
    from PIL import ImageFont
    names = (["DejaVuSans-Bold.ttf", "DejaVuSans.ttf"] if bold
             else ["DejaVuSans.ttf", "DejaVuSans-Bold.ttf"])
    paths = [os.path.join(_ASSETS, "fonts", n) for n in names]
    paths += [f"/usr/share/fonts/truetype/dejavu/{n}" for n in names]
    paths += ["/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
    for p in paths:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _usage_band(canvas: int, tags: list):
    """Pastdagi «QAYERDA ISHLATILADI» bo'lagi — tovar qayerga kerakligini
    darrov ko'rsatadi.

    Nima uchun: xaridor "bu nima" degandan ko'ra "bu menga kerakmi" degan
    savolga javob izlaydi. "Oshxona · Pech · Grill" degan uch so'z rasmning
    o'zida turgani bosishga undaydi.
    """
    from PIL import Image, ImageDraw
    tags = [t.strip() for t in (tags or []) if t and t.strip()][:3]
    if not tags:
        return None, 0

    band_h = int(canvas * 0.155)
    lay = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    top = canvas - band_h

    # Oq yarim-shaffof lenta (fon rangi ustidan yumshoq o'tish)
    d.rectangle([0, top, canvas, canvas], fill=(255, 255, 255, 225))
    d.rectangle([0, top, canvas, top + max(2, canvas // 400)],
                fill=(76, 145, 95, 255))

    label_f = _font(int(canvas * 0.028), bold=False)
    tag_f = _font(int(canvas * 0.042), bold=True)
    label = "QAYERDA ISHLATILADI"
    lw = d.textlength(label, font=label_f)
    d.text(((canvas - lw) / 2, top + canvas * 0.022), label,
           font=label_f, fill=(120, 140, 125, 255))

    # Teglar — yashil «tabletka»lar bo'lib qatorga tiziladi
    pad_x, gap = int(canvas * 0.028), int(canvas * 0.018)
    widths = [d.textlength(t, font=tag_f) + pad_x * 2 for t in tags]
    total = sum(widths) + gap * (len(tags) - 1)
    scale = min(1.0, (canvas - canvas * 0.08) / total) if total else 1.0
    x = (canvas - total * scale) / 2
    y = top + canvas * 0.062
    h = int(canvas * 0.062)
    for t, w in zip(tags, widths):
        w *= scale
        d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2,
                            fill=(233, 246, 236, 255),
                            outline=(160, 205, 175, 255), width=2)
        tw = d.textlength(t, font=tag_f)
        d.text((x + (w - tw) / 2, y + h * 0.5 - canvas * 0.028), t,
               font=tag_f, fill=(30, 92, 55, 255))
        x += w + gap * scale
    return lay, band_h


def _logo(size: int):
    """Logotip — oq foni shaffof qilingan RGBA."""
    global _logo_cache
    from PIL import Image
    if _logo_cache is not None:
        return _logo_cache.resize((size, size), Image.LANCZOS)
    if not os.path.isfile(LOGO_PATH):
        logger.warning("🖼 Logotip topilmadi: %s", LOGO_PATH)
        return None
    try:
        lg = Image.open(LOGO_PATH).convert("RGBA")
        # Oq fonni shaffofga aylantiramiz — logotip fonga "yopishmasin".
        px = lg.load()
        w, h = lg.size
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if r > 238 and g > 238 and b > 238:
                    px[x, y] = (r, g, b, 0)
        _logo_cache = lg
        return lg.resize((size, size), Image.LANCZOS)
    except Exception as e:
        logger.warning("🖼 Logotip o'qilmadi: %s", e)
        return None


def _shadow(obj, canvas_size: int):
    """Tovar tagiga yumshoq soya — kesilgan tovar "yopishtirilgan" ko'rinmasin."""
    from PIL import Image, ImageFilter
    alpha = obj.getchannel("A")
    sh = Image.new("RGBA", obj.size, (25, 60, 35, 0))
    sh.putalpha(alpha.point(lambda v: int(v * 0.33)))
    return sh.filter(ImageFilter.GaussianBlur(canvas_size // 55))


# ─── Sahna fonlari («tovar o'z joyida») ────────────────────────────────────
# Tekis yashil fon toza, lekin quruq. Tovarni HAQIQIY joyiga qo'yish (oshxona
# peshtaxtasi, hammom javoni, ustaxona stoli) xaridorga "bu menga kerak" deb
# aytadi. Sahnalar BIR MARTA yasalgan va `assets/scenes/` da yotadi — har
# tovarga AI chaqirilmaydi, shuning uchun tekin va bir xil uslubda.
SCENES_DIR = os.path.join(_ASSETS, "scenes")

# «Qayerda ishlatiladi» tegi → sahna fayli
TAG_SCENE = {
    "oshxona": "oshxona", "hammom": "hammom", "hojatxona": "hojatxona",
    "yotoqxona": "yotoqxona", "mehmonxona": "mehmonxona", "balkon": "balkon",
    "bog'": "bog", "bog": "bog", "hovli": "hovli", "garaj": "garaj",
    "avtomobil": "avtomobil", "ofis": "ofis", "do'kon": "dokon",
    "ustaxona": "ustaxona", "qurilish": "qurilish", "ta'mirlash": "tamirlash",
    "devor": "devor", "shift": "devor", "pol": "tamirlash",
    "eshik": "tamirlash", "deraza": "tamirlash", "yog'och": "yogoch",
    "metall": "metall", "beton": "qurilish", "plitka": "tamirlash",
    "suv": "hammom", "elektr": "elektr", "isitish": "umumiy",
    "tozalash": "oshxona", "kir yuvish": "kir_yuvish",
    "idish yuvish": "idish_yuvish", "bolalar": "bolalar", "sport": "sport",
    "dala": "bog", "kamin": "mehmonxona", "kundalik": "hammom",
    "yo'lda": "avtomobil", "ish": "ustaxona", "dasturxon": "mehmonxona",
    "uy": "mehmonxona",
}

# Bo'lim → sahna (teg bo'lmasa ishlatiladi)
CATEGORY_SCENE = {
    "elektr asboblar": "ustaxona", "elektr mollari": "elektr",
    "santexnika": "hammom", "bo'yoq va lak": "devor",
    "qurilish aralashmasi": "qurilish", "sochma material": "qurilish",
    "mahkamlagichlar": "ustaxona", "asboblar": "ustaxona",
    "eshik va deraza": "tamirlash", "isitish va gaz": "umumiy",
    "maishiy texnika": "mehmonxona", "oshxona buyumlari": "oshxona",
    "uy-ro'zg'or": "mehmonxona", "tozalash vositalari": "oshxona",
    "shaxsiy parvarish": "hammom", "ish kiyimi va himoya": "qurilish",
}

# Yaltiroq sirtli sahnalarda tovarning AKSI ko'rinadi (marmar, plitka).
_GLOSSY = {"oshxona", "hammom", "hojatxona", "idish_yuvish", "dokon",
           "kir_yuvish", "umumiy", "ofis"}


def scene_path(tags=None, category: str = "") -> str:
    """Teg yoki bo'limga mos sahna fayli. Topilmasa '' (yashil fon ishlatiladi)."""
    keys = [str(t).strip().lower() for t in (tags or []) if str(t).strip()]
    names = [TAG_SCENE[k] for k in keys if k in TAG_SCENE]
    cat = (category or "").strip().lower()
    if cat in CATEGORY_SCENE:
        names.append(CATEGORY_SCENE[cat])
    names.append("umumiy")
    for n in names:
        p = os.path.join(SCENES_DIR, f"{n}.jpg")
        if os.path.isfile(p):
            return p
    return ""


def _scene_bg(path: str, canvas: int):
    """Sahna rasmini kvadratga kesib, kanvas o'lchamiga keltiradi."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    s = min(im.size)
    im = im.crop(((im.width - s) // 2, (im.height - s) // 2,
                  (im.width + s) // 2, (im.height + s) // 2))
    return im.resize((canvas, canvas), Image.LANCZOS).convert("RGBA")


def _reflection(obj, strength: float):
    """Yaltiroq sirtdagi aks — pastga qaragan, tez so'nadigan nusxa."""
    from PIL import Image, ImageDraw, ImageFilter
    ref = obj.transpose(Image.FLIP_TOP_BOTTOM)
    grad = Image.new("L", ref.size)
    gd = ImageDraw.Draw(grad)
    fade = max(1, int(ref.height * 0.45))
    for yy in range(ref.height):
        gd.line([(0, yy), (ref.width, yy)],
                fill=int(255 * strength * max(0.0, 1 - yy / fade)))
    ref.putalpha(Image.composite(grad, Image.new("L", ref.size, 0),
                                 ref.getchannel("A")))
    return ref.filter(ImageFilter.GaussianBlur(2))


def compose_scene(cut, scene_file: str, canvas: int = CANVAS, tags=None):
    """Kesilgan tovarni sahnaga qo'yadi: aks + tushuvchi soya + kontakt soya.

    Uchala soya birgalikda tovarni "yopishtirilgan" emas, o'sha yerda TURGAN
    qilib ko'rsatadi — busiz kollaj sun'iy ko'rinadi.
    """
    from PIL import Image, ImageDraw, ImageFilter

    bg = _scene_bg(scene_file, canvas)
    band, band_h = _usage_band(canvas, tags)
    free_h = canvas - band_h

    h = int(free_h * 0.62)
    w = max(1, int(cut.width * h / cut.height))
    if w > canvas * 0.62:
        w = int(canvas * 0.62)
        h = max(1, int(cut.height * w / cut.width))
    obj = cut.resize((w, h), Image.LANCZOS)
    bx = (canvas - w) // 2
    base = int(free_h * 0.94)            # tovar TAGI shu chiziqda turadi
    by = base - h

    glossy = os.path.splitext(os.path.basename(scene_file))[0] in _GLOSSY
    if glossy:
        bg.alpha_composite(_reflection(obj, 0.26), (bx, base))

    # Tushuvchi soya — yorug'lik chapdan tushadi deb hisoblaymiz
    drop = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    sil = Image.new("RGBA", obj.size, (32, 30, 28, 0))
    sil.putalpha(obj.getchannel("A").point(lambda v: int(v * 0.28)))
    drop.alpha_composite(sil, (bx + canvas // 45, by + canvas // 90))
    bg.alpha_composite(drop.filter(ImageFilter.GaussianBlur(canvas // 45)))

    # Kontakt soya — tagida tor va qorong'iroq
    con = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    cd = ImageDraw.Draw(con)
    cw, ch = int(w * 0.88), int(canvas * 0.020)
    cd.ellipse([bx + (w - cw) // 2, base - ch // 2,
                bx + (w + cw) // 2, base + ch // 2], fill=(18, 16, 14, 155))
    bg.alpha_composite(con.filter(ImageFilter.GaussianBlur(canvas // 120)))

    bg.alpha_composite(obj, (bx, by))
    if band is not None:
        bg.alpha_composite(band)

    lg_size = int(canvas * LOGO_RATIO)
    lg = _logo(lg_size)
    if lg is not None:
        m = int(canvas * LOGO_MARGIN)
        pos = (canvas - lg_size - m, m)
        # Sahna qorong'i bo'lishi mumkin (g'ishtli devor, ustaxona) — logotip
        # yo'qolib ketmasin uchun tagiga yumshoq oq doira qo'yamiz.
        pad = int(lg_size * 0.12)
        plate = Image.new("RGBA", (lg_size + pad * 2, lg_size + pad * 2),
                          (0, 0, 0, 0))
        ImageDraw.Draw(plate).ellipse([0, 0, plate.width - 1, plate.height - 1],
                                      fill=(255, 255, 255, 205))
        bg.alpha_composite(plate.filter(ImageFilter.GaussianBlur(pad // 2)),
                           (pos[0] - pad, pos[1] - pad))
        bg.alpha_composite(lg, pos)
    return bg.convert("RGB")


def compose(im, cut=None, canvas: int = CANVAS, tags=None):
    """Rasmni brend fonga joylaydi. `cut` — foni kesilgan RGBA (bo'lmasa None).

    `tags` — tovar QAYERDA ishlatilishi (2-3 so'z): pastdagi lentada chiqadi.

    Kesilgan bo'lsa: tovar chegarasi bo'yicha qirqiladi va markazga qo'yiladi.
    Kesilmagan bo'lsa: asl rasm to'liq sig'diriladi (letterbox) — hech narsa
    qirqilmaydi, chunki katalogda yarim tovar ko'rsatishdan yomoni yo'q.
    """
    from PIL import Image
    bg = _background(canvas).convert("RGBA")

    src = cut if cut is not None else im.convert("RGBA")
    if cut is not None:
        box = src.getchannel("A").getbbox()
        if box:
            src = src.crop(box)

    # Lenta bor bo'lsa tovarga qolgan bo'sh joy kichrayadi.
    band, band_h = _usage_band(canvas, tags)
    free_h = canvas - band_h
    limit_w = int(canvas * (0.82 if band_h else OBJECT_RATIO))
    limit_h = int(free_h * 0.86) if band_h else int(canvas * OBJECT_RATIO)

    # DIQQAT: `thumbnail` faqat kichraytiradi. Kesilgan tovar kichik chiqsa
    # (masalan 700px) kanvasda yo'qolib qolardi — shuning uchun o'zimiz
    # miqyoslaymiz, kerak bo'lsa kattalashtiramiz ham (1.8 barobargacha;
    # undan ortig'i xiralashadi).
    sc = min(limit_w / max(1, src.width), limit_h / max(1, src.height), 1.8)
    if abs(sc - 1.0) > 0.01:
        src = src.resize((max(1, int(src.width * sc)),
                          max(1, int(src.height * sc))), Image.LANCZOS)
    x = (canvas - src.width) // 2
    if band_h:
        # Tovar lentaning ustida "tursin" — pastga bosilgan kompozitsiya
        # bo'sh osmonni yo'qotadi va tovar kattaroq ko'rinadi.
        y = free_h - src.height - int(canvas * 0.045)
    else:
        y = (free_h - src.height) // 2

    if cut is not None:
        # Taglik ellipsi + tovarning o'z soyasi — hajm hissi paydo bo'ladi.
        bg.alpha_composite(_podium(canvas, src.width, y + src.height))
        sh = _shadow(src, canvas)
        bg.alpha_composite(sh, (x, y + canvas // 60))
    bg.alpha_composite(src, (x, y))

    if band is not None:
        bg.alpha_composite(band)

    lg_size = int(canvas * LOGO_RATIO)
    lg = _logo(lg_size)
    if lg is not None:
        m = int(canvas * LOGO_MARGIN)
        # Lenta bo'lsa logotip yuqori o'ng burchakka ko'chadi (lentani
        # bosib qolmasin), aks holda pastda qoladi.
        pos = ((canvas - lg_size - m, m) if band_h
               else (canvas - lg_size - m, canvas - lg_size - m))
        bg.alpha_composite(lg, pos)
    return bg.convert("RGB")


# ─── Asosiy funksiya ───────────────────────────────────────────────────────

def brand(raw: bytes, canvas: int = CANVAS, tags=None,
          category: str = "", scene: bool = True) -> tuple:
    """Asl rasm baytlari → (brend rasm JPEG baytlari, ma'lumot dict).

    `tags` — "qayerda ishlatiladi" so'zlari (2-3 ta): rasm pastidagi lentaga
    chiqadi VA tovar qaysi sahnaga (oshxona/hammom/ustaxona) qo'yilishini
    belgilaydi. `scene=False` — eski tekis yashil fon.

    dict: {"ok": bool, "cut": bool, "error": str}
      ok=False bo'lsa chaqiruvchi ASL rasmni ishlatishi kerak — hech narsa
      buzilmaydi, faqat rasm brendlanmagan bo'ladi.
    """
    info = {"ok": False, "cut": False, "scene": "", "error": ""}
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)          # telefon rasmi aylanmasin
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        # Kesish uchun juda katta rasm shart emas — tezlik va xotira uchun.
        work = im.copy()
        if max(work.size) > 1600:            # xotira uchun yuqori chegara
            work.thumbnail((1600, 1600), Image.LANCZOS)

        cut = cutout(work)
        info["cut"] = cut is not None

        # Fon kesilgan bo'lsa — tovarni HAQIQIY sahnaga qo'yamiz.
        # Kesilmagan rasmni sahnaga qo'yib bo'lmaydi (to'rtburchak bo'lib
        # yopishib qoladi), u holda eski yashil fon ishlatiladi.
        sfile = scene_path(tags, category) if (scene and cut is not None) else ""
        if sfile:
            box = cut.getchannel("A").getbbox()
            obj = cut.crop(box) if box else cut
            out = compose_scene(obj, sfile, canvas, tags)
            info["scene"] = os.path.splitext(os.path.basename(sfile))[0]
        else:
            out = compose(work, cut, canvas, tags)

        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        info["ok"] = True
        return buf.getvalue(), info
    except Exception as e:
        logger.warning("🖼 Brendlash bo'lmadi (asl rasm qoladi): %s", e)
        info["error"] = str(e)
        return raw, info


def brand_file(src_path: str, dst_path: str, canvas: int = CANVAS,
               tags=None, category: str = "", scene: bool = True) -> dict:
    """Fayldan faylga — to'plamli qayta ishlash skriptlari uchun."""
    with open(src_path, "rb") as f:
        raw = f.read()
    data, info = brand(raw, canvas, tags, category, scene)
    if not info["ok"]:
        return info
    with open(dst_path, "wb") as f:
        f.write(data)
    return info


# ─── Alohida jarayonda ishlatish ───────────────────────────────────────────
# Sifatli model (u2net, 176 MB) botning o'zida yuklansa, xotirada DOIMIY
# qoladi va 1 GB li serverda joy qolmaydi. Shuning uchun rasm alohida qisqa
# umrli jarayonda ishlanadi: jarayon tugashi bilan xotira OS ga qaytadi.
# Jarayon yiqilsa (xotira yetmasa) — yengil model bilan qayta uriniladi.

def brand_file_isolated(src_path: str, dst_path: str, tags=None,
                        category: str = "", model: str = "",
                        timeout: int = 240) -> dict:
    """`brand_file` ni alohida jarayonda bajaradi. Natija: o'sha dict."""
    import json as _json
    import subprocess
    import sys

    args = [sys.executable, "-m", "bot.imageproc", src_path, dst_path,
            "--tags", ",".join(tags or []), "--category", category or ""]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _run(mdl: str) -> dict:
        env = dict(os.environ)
        if mdl:
            env["CUTOUT_MODEL"] = mdl
        p = subprocess.run(args, cwd=root, env=env, capture_output=True,
                           timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError(
                f"kod={p.returncode} {p.stderr.decode()[-200:]}")
        return _json.loads(p.stdout.decode().strip().splitlines()[-1])

    first = model or os.getenv("CUTOUT_MODEL", "") or CUTOUT_MODEL
    try:
        return _run(first)
    except Exception as e:
        logger.warning("🖼 %s bilan bo'lmadi (%s) — u2netp bilan qayta", first, e)
    try:
        return _run("u2netp")
    except Exception as e:
        logger.error("🖼 Alohida jarayonda ham bo'lmadi: %s", e)
        return {"ok": False, "cut": False, "scene": "", "error": str(e)}


def _cli(argv=None) -> int:
    """`python -m bot.imageproc <asl> <natija> [--tags a,b] [--category X]`"""
    import argparse
    import json as _json
    ap = argparse.ArgumentParser(description="Rasmni brendlash (bitta fayl)")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--tags", default="")
    ap.add_argument("--category", default="")
    ap.add_argument("--no-scene", action="store_true")
    a = ap.parse_args(argv)
    tags = [t.strip() for t in a.tags.split(",") if t.strip()]
    info = brand_file(a.src, a.dst, tags=tags, category=a.category,
                      scene=not a.no_scene)
    print(_json.dumps(info))
    return 0 if info.get("ok") else 1


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli())
