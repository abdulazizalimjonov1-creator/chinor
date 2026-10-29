"""Sennik (narx yorlig'i) tekshiruvi — keyingi o'zgarishlar buzib qo'ymasin.

Nega kerak: sennik uch qismdan iborat va ular bir-biriga bog'liq —
shtrix-kod chizg'ichi, printer uchun rasm (TSPL) va brauzer uchun HTML.
Birini o'zgartirib, ikkinchisini buzib qo'yish oson, xato esa faqat
PRINTERDA, ish ustida bilinadi. Shu skript o'sha uchalasini bir zumda
tekshiradi.

Ishga tushirish (hech qanday server, baza yoki internet kerak emas):

    python3 chinor-bot/tests/test_sennik.py

Shtrix-kodni HAQIQIY dekoder bilan tekshirish ham bor — `zxing-cpp`
o'rnatilgan bo'lsa o'zi ishlaydi, bo'lmasa o'sha tekshiruv o'tkazib
yuboriladi (qolganlari baribir ishlaydi).
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # chinor-bot/
os.environ.setdefault("DATA_DIR", os.path.join(HERE, "_data"))

_fail = []


def check(nom: str, shart: bool, izoh: str = "") -> None:
    print(f"  {'✅' if shart else '❌'} {nom}{(' — ' + izoh) if izoh and not shart else ''}")
    if not shart:
        _fail.append(nom)


NAMUNA = [
    # (tovar, kutilgan kod) — kodsiz tovarga do'kon ichki kodi yasaladi
    ({"name": "Knauf Rotband gips shpatlovka 30 kg", "price": 78000, "sku": 10234,
      "unit": "dona", "barcode": ""}, "10234"),
    ({"name": "Arra yog'och 40sm LIT-185895", "price": 33000, "sku": 10012,
      "unit": "dona", "barcode": "6985560675353"}, "6985560675353"),
    ({"name": "Fasad bo'yog'i akril oq mat 25 kg vedro premium sifat",
      "price": 1250000, "sku": 10456, "unit": "dona", "barcode": ""}, "10456"),
]


def test_barcode() -> None:
    print("\n1) Shtrix-kod chizg'ichi")
    from bot import barcode_gen as bg

    # Adabiyotdagi ma'lum to'g'ri kodlar
    for code in ("4006381333931", "5901234123457"):
        check(f"{code} to'g'ri deb tanildi", bg.is_valid_ean13(code))
        check(f"{code} 95 modul", len(bg.bits(code)) == 95)
    check("nazorat raqami xato kod rad etildi", not bg.is_valid_ean13("4006381333932"))

    bits = bg.bits("5901234123457")
    check("chap qo'riqchi", bits[:3] == [1, 0, 1])
    check("o'rta qo'riqchi", bits[45:50] == [0, 1, 0, 1, 0])
    check("o'ng qo'riqchi", bits[-3:] == [1, 0, 1])

    # Ichki kod: id → kod → id (sennik skanerlanganda tovar shu bilan topiladi)
    for pid in (7, 10011, 10234, 99999):
        code = bg.internal_code(pid)
        check(f"ichki kod #{pid} ({code})",
              bg.is_valid_ean13(code) and bg.sku_from_code(code) == pid)
    check("zavod kodi ichki deb o'qilmadi", bg.sku_from_code("4006381333931") == 0)

    for lb, kutilgan in NAMUNA:
        got, _kind = bg.for_product(lb["barcode"], lb["sku"])
        check(f"«{lb['name'][:22]}…» kodi {kutilgan}", got == kutilgan, f"chiqdi {got}")


def test_tspl() -> None:
    print("\n2) Printer uchun rasm va TSPL buyruqlari")
    try:
        from bot import print_agent
    except ImportError as e:
        check("print_agent import", False, str(e))
        return

    for size, (mm_w, mm_h) in (("60x40", (60, 40)), ("30x20", (30, 20))):
        lb = NAMUNA[0][0]
        im = print_agent.render_label(lb, size)
        check(f"{size}: rasm o'lchami {mm_w*8}×{mm_h*8}",
              im.size == (mm_w * 8, mm_h * 8), f"chiqdi {im.size}")
        check(f"{size}: oq-qora (1 bit)", im.mode == "1")
        # Bo'sh chiqib qolmasin: qora nuqtalar bo'lishi shart
        qora = sum(1 for p in im.getdata() if p == 0)
        check(f"{size}: sennik bo'sh emas ({qora} nuqta)", qora > im.width * im.height * 0.02)

        data = print_agent.tspl_label(lb, size)
        head = data[:200].decode("ascii", "ignore")
        check(f"{size}: SIZE buyrug'i", f"SIZE {mm_w} mm,{mm_h} mm" in head)
        check(f"{size}: GAP buyrug'i", "GAP " in head)
        check(f"{size}: PRINT bilan tugaydi", data.rstrip().endswith(b"PRINT 1,1"))
        m = re.search(rb"BITMAP (\d+),(\d+),(\d+),(\d+),(\d+),", data)
        check(f"{size}: BITMAP sarlavhasi", m is not None)
        if m:
            wb, h = int(m.group(3)), int(m.group(4))
            kutilgan = (im.width + 7) // 8 * im.height
            tana = len(data) - m.end() - len(b"\r\nPRINT 1,1\r\n")
            check(f"{size}: rasm baytlari to'liq ({kutilgan})",
                  wb * h == kutilgan and tana == kutilgan, f"chiqdi {tana}")


def test_skaner() -> None:
    print("\n3) Shtrix-kod HAQIQIY dekoder bilan o'qiladimi")
    try:
        import zxingcpp
    except ImportError:
        print("  ⏭  zxing-cpp o'rnatilmagan — o'tkazib yuborildi "
              "(pip install zxing-cpp)")
        return
    from bot import print_agent

    for size in ("60x40", "30x20"):
        for lb, kutilgan in NAMUNA:
            im = print_agent.render_label(lb, size)
            topildi = [r.text for r in zxingcpp.read_barcodes(im.convert("L")) if r.valid]
            check(f"{size}: {kutilgan} o'qildi", kutilgan in topildi,
                  f"chiqdi {topildi or 'hech narsa'}")


def test_html() -> None:
    print("\n4) Brauzer uchun sennik (HTML)")
    try:
        from bot import api_pricetag
    except ImportError as e:
        check("api_pricetag import", False, str(e))
        return

    for size in api_pricetag.SIZES:
        html = api_pricetag._tag_html(NAMUNA[1][0], size)
        check(f"{size}: nomi bor", "Arra yog" in html)
        check(f"{size}: narxi bor", "33 000" in html)
        check(f"{size}: shtrix-kod chizmasi bor", "<svg" in html)
        if not api_pricetag.SIZES[size].get("mm") or size != "30x20":
            check(f"{size}: SKU bor", "SKU" in html or size == "30x20")
        check(f"{size}: logotip bor", "logo.png" in html or not api_pricetag._logo_bytes())

    sahifa = api_pricetag._page_html({"size": "60x40", "labels": [NAMUNA[0][0]], "at": 0})
    check("sahifada @page o'lchami", "@page{size:60mm 40mm" in sahifa.replace(" {", "{"))
    check("sahifada chop etish tugmasi", "window.print()" in sahifa)


def main() -> int:
    print("SENNIK TEKSHIRUVI")
    test_barcode()
    test_tspl()
    test_skaner()
    test_html()
    print()
    if _fail:
        print(f"❌ {len(_fail)} ta tekshiruv O'TMADI:")
        for f in _fail:
            print("   -", f)
        return 1
    print("✅ hammasi joyida")
    return 0


if __name__ == "__main__":
    sys.exit(main())
