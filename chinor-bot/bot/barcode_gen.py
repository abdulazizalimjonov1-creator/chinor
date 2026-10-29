"""Sennik uchun EAN-13 shtrix-kod chizmasi (kutubxonasiz, sof Python).

Nega EAN-13 va faqat shu:
  • Do'kondagi 7000 tovardan 2000 tasida zavod shtrix-kodi bor — u aynan
    EAN-13 (13 raqam).
  • Qolganlarida kod yo'q. Ular uchun DO'KON ICHKI kodini yasaymiz:
    `200` + tovar id si (9 raqam) + nazorat raqami. `20…29` oralig'i
    standart bo'yicha aynan do'kon ichki kodlari uchun ajratilgan, ya'ni
    hech bir zavod kodi bilan to'qnashmaydi.
  • Bitta simvolika — bitta chizg'ich, bitta tekshiruv. Code128 jadvali
    107 qatorli, xatosi esa faqat skanerda bilinadi.

Skaner ichki kodni o'qiganda `bot/web_api` tovarni id bo'yicha topadi
(`sku_from_code`).
"""

from __future__ import annotations

# 1 = qora chiziq, 0 = oq. L kodlar 0 bilan, R kodlar 1 bilan boshlanadi.
_L = ("0001101", "0011001", "0010011", "0111101", "0100011",
      "0110001", "0101111", "0111011", "0110111", "0001011")
_G = ("0100111", "0110011", "0011011", "0100001", "0011101",
      "0111001", "0000101", "0010001", "0001001", "0010111")
_R = ("1110010", "1100110", "1101100", "1000010", "1011100",
      "1001110", "1010000", "1000100", "1001000", "1110100")
# Birinchi raqam chap yarmidagi L/G tartibi bilan "yashiringan"
_PARITY = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
           "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")

MODULES = 95                      # EAN-13 doim 95 modul
EAN13, CODE128 = "ean13", "code128"
INTERNAL_PREFIX = "200"           # do'kon ichki kodlari


def checksum(d12: str) -> int:
    """EAN-13 nazorat raqami (12 ta raqamdan)."""
    s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(d12))
    return (10 - s % 10) % 10


def is_valid_ean13(code: str) -> bool:
    code = (code or "").strip()
    return (len(code) == 13 and code.isdigit()
            and int(code[12]) == checksum(code[:12]))


def internal_code(product_id: int) -> str:
    """Shtrix-kodi yo'q tovar uchun do'kon ichki EAN-13 i."""
    body = f"{INTERNAL_PREFIX}{int(product_id):09d}"[:12]
    return body + str(checksum(body))


def sku_from_code(code: str) -> int:
    """Ichki kod bo'lsa — tovar id si, aks holda 0."""
    code = (code or "").strip()
    if not is_valid_ean13(code) or not code.startswith(INTERNAL_PREFIX):
        return 0
    try:
        return int(code[len(INTERNAL_PREFIX):12])
    except ValueError:
        return 0


def bits(code: str) -> list:
    """EAN-13 ning 95 moduli: 1 = qora, 0 = oq."""
    code = (code or "").strip()
    if len(code) == 12 and code.isdigit():
        code += str(checksum(code))
    if not is_valid_ean13(code):
        raise ValueError(f"EAN-13 emas: {code!r}")
    d = [int(c) for c in code]
    out = [1, 0, 1]                                   # chap qo'riqchi
    for i, digit in enumerate(d[1:7]):
        table = _L if _PARITY[d[0]][i] == "L" else _G
        out += [int(b) for b in table[digit]]
    out += [0, 1, 0, 1, 0]                            # o'rta qo'riqchi
    for digit in d[7:]:
        out += [int(b) for b in _R[digit]]
    out += [1, 0, 1]                                  # o'ng qo'riqchi
    return out


def for_product(barcode: str, product_id: int) -> tuple:
    """(kod, turi) — sennikka nima chiqishi.

    • Zavod kodi bor → o'sha EAN-13. Kassa uni allaqachon biladi.
    • Yo'q → tovarning O'Z raqami (SKU) Code128 da. Skaner aynan "15507"
      deb yuboradi va kassa uni artikul bo'yicha topadi.

    Ilgari bu yerda `200`+id dan EAN-13 yasalardi, lekin skaner uni
    "2000000155077" deb yuborar, bunday artikul esa YesPOS kassasida yo'q
    edi — ya'ni kod o'qilsa ham tovar topilmasdi."""
    bc = (barcode or "").strip()
    if is_valid_ean13(bc):
        return bc, EAN13
    # 12 raqamli (nazorat raqamisiz) kod ham uchraydi — to'ldiramiz
    if len(bc) == 12 and bc.isdigit():
        return bc + str(checksum(bc)), EAN13
    # Boshqa uzunlikdagi zavod kodi (EAN-8, UPC va h.k.) — borligicha,
    # Code128 da: skaner baribir xuddi shu raqamlarni yuboradi.
    if bc.isdigit() and 4 <= len(bc) <= 18:
        return bc, CODE128
    return str(int(product_id or 0)), CODE128


def bits_for(code: str, kind: str) -> list:
    """Kod turiga qarab chizma (1 = qora)."""
    return bits(code) if kind == EAN13 else code128_bits(code)



# ── Code 128 ───────────────────────────────────────────────────────────────
# Zavod kodi yo'q tovarlarga tovarning O'Z raqami (SKU) chiqadi: skaner
# aynan "15507" deb yuboradi va kassa uni artikul bo'yicha topadi.
# EAN-13 bunga yaramaydi — u 13 raqamga to'ldirishni talab qiladi va skaner
# "2000000155077" deb yuboradi, bunday artikul esa hech qayerda yo'q.
#
# Jadval: har bir belgi 11 moduldan iborat, 6 ta kenglik bilan beriladi
# (chiziq, bo'shliq, chiziq, bo'shliq, chiziq, bo'shliq).
_C128 = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213",
    "122312", "132212", "221213", "221312", "231212", "112232", "122132",
    "122231", "113222", "123122", "123221", "223211", "221132", "221231",
    "213212", "223112", "312131", "311222", "321122", "321221", "312212",
    "322112", "322211", "212123", "212321", "232121", "111323", "131123",
    "131321", "112313", "132113", "132311", "211313", "231113", "231311",
    "112133", "112331", "132131", "113123", "113321", "133121", "313121",
    "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114",
    "413111", "241112", "134111", "111242", "121142", "121241", "114212",
    "124112", "124211", "411212", "421112", "421211", "212141", "214121",
    "412121", "111143", "111341", "131141", "114113", "114311", "411113",
    "411311", "113141", "114131", "311141", "411131", "211412", "211214",
    "211232", "2331112",
)
START_B, START_C, CODE_B, CODE_C, STOP = 104, 105, 100, 99, 106


def _c128_values(data: str) -> list:
    """Raqamlarni juft-juft qilib (C to'plami) siqadi — kod qisqa chiqadi,
    30×20 sennikka ham sig'adi."""
    vals, i = [], 0
    digits = data.isdigit()
    if digits and len(data) % 2 == 0:
        vals.append(START_C)
        mode = "C"
    else:
        vals.append(START_B)
        mode = "B"
    while i < len(data):
        qolgan = len(data) - i
        if mode == "B" and data[i:].isdigit() and qolgan >= 4 and qolgan % 2 == 0:
            vals.append(CODE_C)
            mode = "C"
        if mode == "C":
            if qolgan >= 2 and data[i:i + 2].isdigit():
                vals.append(int(data[i:i + 2]))
                i += 2
                continue
            vals.append(CODE_B)
            mode = "B"
        vals.append(ord(data[i]) - 32)
        i += 1
    return vals


def code128_bits(data: str) -> list:
    """Code 128 chizmasi (1 = qora). Faqat oddiy ASCII qabul qiladi."""
    data = (data or "").strip()
    if not data or any(ord(c) < 32 or ord(c) > 126 for c in data):
        raise ValueError(f"Code128 uchun yaroqsiz matn: {data!r}")
    vals = _c128_values(data)
    chk = vals[0] + sum(v * i for i, v in enumerate(vals[1:], start=1))
    vals.append(chk % 103)
    vals.append(STOP)
    out = []
    for v in vals:
        qora = True
        for w in _C128[v]:
            out += [1 if qora else 0] * int(w)
            qora = not qora
    return out


# Qo'riqchi chiziqlar (chap/o'rta/o'ng) pastga uzunroq tushadi — skaner
# shunga qarab kodning chekkasini topadi.
GUARDS = set(range(0, 3)) | set(range(45, 50)) | set(range(92, 95))


def svg(code: str, kind: str, width_mm: float, height_mm: float,
        show_text: bool = True) -> str:
    """Brauzerdagi sennik uchun — chizmani SVG qilib qaytaradi."""
    b = bits_for(code, kind)
    mw = width_mm / len(b)
    bar_h = height_mm - (height_mm * 0.26 if show_text else 0)
    rects = []
    for i, bit in enumerate(b):
        if not bit:
            continue
        uzun = kind == EAN13 and i in GUARDS and show_text
        h = bar_h + (height_mm * 0.07 if uzun else 0)
        rects.append(f'<rect x="{i*mw:.3f}" y="0" width="{mw:.3f}" height="{h:.3f}"/>')
    text = ""
    if show_text:
        text = (f'<text x="{width_mm/2:.2f}" y="{height_mm:.2f}" fill="#000" '
                f'font-family="monospace" font-size="{height_mm*0.24:.2f}" '
                f'text-anchor="middle" letter-spacing="{mw*0.5:.3f}">{code}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm" '
            f'height="{height_mm}mm" viewBox="0 0 {width_mm} {height_mm}" '
            f'shape-rendering="crispEdges">{"".join(rects)}{text}</svg>')
