# Sennik (narx yorlig'i) chop etish

Mini App → **🏷 Sennik chop etish** (admin paneli).

## Qanday ishlaydi

1. Adminda tovar tanlanadi (qidiruv yoki 📷 skaner), har biriga nusxa soni
   qo'yiladi, o'lcham tanlanadi.
2. «Chop etish» bosilganda Mini App serverga ro'yxatni yuboradi
   (`POST /api/admin/pricetags`). Narx, SKU va shtrix-kod **bazadan** olinadi —
   telefonda ochiq turgan eski ro'yxatdagi narx sennikka tushmaydi.
3. Server bir martalik havola qaytaradi: `/sennik?job=<token>` (2 soat yashaydi).
   Havola tashqi brauzerda ochiladi — Telegram ichidagi oynada chop etish
   oynasi (Ctrl+P) yo'q.
4. Sahifa ochilishi bilan chop etish oynasi o'zi chiqadi; yopilib qolsa
   sahifadagi **🖨 Chop etish** tugmasi bor.

Havolani **printer ulangan boshqa kompyuterda** ochish ham mumkin (tokenli,
parolsiz). Shu sababli u qisqa muddatli: 2 soatdan keyin ishlamaydi.

## O'lchamlar

| Kalit   | O'lcham        | Qayerda                                        |
|---------|----------------|------------------------------------------------|
| `60x40` | 60×40 mm       | Ma'lumot qabul qiluvchi kompyuterdagi printer   |
| `30x20` | 30×20 mm       | Server kompyuteridagi printer                   |
| `a4`    | A4, 8 ta       | Oddiy qog'ozda, qirqib olinadi                  |
| `a4big` | A4, 1 ta katta | Javon / aksiya uchun                            |

Sennikda: do'kon logosi, tovar nomi, narxi, SKU (= tovar id), o'lchov birligi
va shtrix-kod raqami (bo'lsa). Uzun nom avtomatik kichrayadi.

## Shtrix-kod (skanerlash uchun)

Har bir sennikda skanerlanadigan kod bo'ladi:

| Holat | Nima bosiladi | Skaner nima yuboradi |
|---|---|---|
| Zavod kodi bor (~2000 tovar) | o'sha **EAN-13** | `4780069000833` |
| Kodi yo'q (~5000 tovar) | tovarning **O'Z raqami (SKU)**, Code 128 da | `15507` |

**Nega shunday.** Avval kodsiz tovarlarga `200`+id dan EAN-13 yasalardi
(do'kon ichki kodi). Chizma to'g'ri edi, lekin skaner kassaga
`2000000155077` deb yuborardi — bunday artikul YesPOS'da yo'q, ya'ni kod
o'qilsa ham tovar topilmasdi. Endi SKU ning o'zi bosiladi va kassa uni
artikul bo'yicha topadi.

Eski, `200…` li senniklar ham ishlayveradi: qidiruvda ularni tovarga
bog'laydigan qadam saqlab qolingan (`database/_products.py:_internal_sku`).

Chizg'ich: `bot/barcode_gen.py` — kutubxonasiz, EAN-13 ham, Code 128 ham.
Chop etishda modul (eng ingichka chiziq) eni BUTUN nuqta bo'ladi: kasr
bo'lsa printer goh 2, goh 3 nuqta bosadi va kod o'qilmay qoladi.

## Chekkadagi bo'sh joy

Termal printer yorliqqa bir necha nuqta surilib bosishi odatiy hol —
shuning uchun mazmun chekkadan 3 mm (60×40) va 1.5 mm (30×20) ichkarida
chiziladi. Bu bo'shliqni kamaytirmang: 2026-10-29 da aynan shu sababdan
`SKU 15507` ning boshi kesilib chiqqan edi.

## Ikki yo'l: to'g'ridan-to'g'ri printerga yoki brauzer orqali

XP-365B **USB** bilan ulangan — serverdan unga to'g'ridan-to'g'ri yozib
bo'lmaydi. Shuning uchun printer ulangan kompyuterda kichik agent dasturi
turadi (`deploy/windows/sennik-agent/`).

| Tanlov | Nima bo'ladi |
|--------|--------------|
| **Kompyuter nomi** (🟢) | Sennik o'sha zahoti printerdan chiqadi. Hech qanday oyna ochilmaydi — kompyuterdagi odamga xalaqit bermaydi. |
| **Brauzer orqali** | Havola ochiladi, printer qo'lda tanlanadi. A4 uchun yagona yo'l. |

Oqim: Mini App → server sennikni rasmga chizadi (Pillow) va TSPL `BITMAP`
buyrug'iga o'raydi → agent navbatdan oladi → `winspool` orqali printerga
XOM (RAW) bayt bo'lib ketadi.

Nega rasm: printerning ichki shriftida o'zbekcha matn ham, logotip ham
yo'q. Rasm esa Mini App'dagi ko'rinishning aynan o'zi.

Nega agent "hech narsa so'ramaydi": u `wscript` orqali ko'rinmas holda
ishga tushadi, serverdan javob kutib turadi (protsessor ~0%), xatolarni
faqat `agent.log` ga yozadi.

### Agentni o'rnatish

`deploy/windows/sennik-agent/README.md` ga qarang. Qisqacha: papkani
kompyuterga ko'chirib, bir marta:

```powershell
.\install.ps1 -Server https://kassa.chinorpos.com -Token <KALIT> -Key qabul -Name "Qabul kompyuteri"
```

KALIT — serverdagi `DATA_DIR/print_agent_token.txt` fayli (bot o'zi
yaratadi; `.env` dagi `PRINT_AGENT_TOKEN` bilan ham berish mumkin).
Har kompyuterga o'z `-Key` i beriladi.

## Printer sozlamasi (Xprinter XP-365B, 203 DPI)

Bu sozlama faqat **brauzer orqali** chop etishga kerak; agent orqali
chiqarilsa, o'lcham va tanaffus TSPL buyrug'ida ketadi.

Bir marta sozlanadi:

1. Windows'da printer drayveri o'rnatilgan bo'lsin.
2. **Printing Preferences** → yorliq o'lchami 60×40 mm (yoki 30×20 mm) qilib
   qo'yiladi, `Gap` (yorliqlar orasi) kalibrlanadi.
3. Brauzerning chop etish oynasida:
   - **Printer** — XP-365B
   - **Qog'oz o'lchami** — 60×40 mm (yoki 30×20 mm)
   - **Chegara (Margins)** — «Yo'q / None»
   - **Masshtab (Scale)** — 100% («Fit to page» EMAS, aks holda yorliq kichrayadi)
   - **Kolontitul (Headers and footers)** — o'chirilgan (aks holda yorliqqa
     sana va havola bosilib chiqadi)

Chrome tanlangan printer va sozlamani shu sayt uchun eslab qoladi — keyin
faqat «Print» bosiladi.

## Tekshirish (o'zgartirgandan keyin SHART)

```bash
python3 chinor-bot/tests/test_sennik.py
```

Shtrix-kod chizg'ichi, printer uchun TSPL va brauzer uchun HTML — uchalasi
ham bir zumda tekshiriladi, server ham, baza ham, internet ham kerak emas.
`zxing-cpp` o'rnatilgan bo'lsa (`pip install zxing-cpp`), chizilgan kodlar
HAQIQIY dekoder bilan o'qib ko'riladi — ya'ni «skaner o'qiydimi» degan
savolga javob beradi.

## Texnik tafsilot

- Kod: `bot/api_pricetag.py` (ko'rinish + navbat), `bot/print_agent.py`
  (rasm → TSPL, agent endpointlari), `app/app.js` — `openPriceTags()`.
- Sennik ko'rinishi FAQAT serverda chiziladi; Mini App uni qayta chizmaydi,
  shuning uchun ikki joyda ikki xil ko'rinish paydo bo'lmaydi.
- Logotip `/sennik/logo.png` manzilidan keladi (`chinor-bot/assets/logo.png`).
  CSS fon rasmi sifatida qo'yilmagan: Chrome fon rasmlarini sukut bo'yicha
  chop etmaydi, logo esa qolib ketmasligi kerak.
- Ruxsat: `products_view` (to'liq admin va «📦 Mahsulotchi» rolida bor).
- Bir buyurtmada eng ko'pi 400 ta sennik.
