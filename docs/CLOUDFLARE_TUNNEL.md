# Cloudflare Tunnel — ngrok o'rniga doimiy URL

ngrok pullik bo'lgani uchun server (Mac, `main.py`, port **8765**) endi
**Cloudflare Tunnel** orqali internetga chiqadi. Bitta tunnel hammasini ochadi:
sync (sotuv/katalog/cheklar) · Mini App frontend · Windows/macOS yangilanish feed
(`/updates/`).

> **Muhim:** Windows kassalar va yangilanish feed uchun **o'zgarmas (doimiy) URL**
> shart. `cloudflared tunnel --url ...` (quick tunnel) har safar yangi
> `*.trycloudflare.com` beradi — bu yaramaydi. Doimiy URL uchun
> **Cloudflare'ga ulangan domen** kerak. Quyida shu yo'l.

---

## 1-QADAM — Cloudflare'da domen (bir martalik)

Tunnel'ga doimiy nom berish uchun Cloudflare'da bitta domen (zone) bo'lishi kerak.
`*.pages.dev` bunga yaramaydi — haqiqiy domen kerak.

**Eng oson yo'l — Cloudflare Registrar:**
1. https://dash.cloudflare.com → **Domain Registration → Register Domains**
2. Arzon domen tanlang (masalan `.xyz`, `.top` ~ $5–10/yil; yoki `.com` ~$10/yil).
3. Sotib oling — domen avtomatik sizning Cloudflare account'ingizga zone bo'lib
   qo'shiladi (nameserver o'zgartirish shart emas).

**Yoki boshqa joydan (Namecheap/Porkbun) olsangiz:** domenni Cloudflare'ga
qo'shing (Add a site) va registrar panelida **nameserver**larni Cloudflare
bergan ikkita NS'ga almashtiring. Faollashishini kuting (odatda < 1 soat).

Bu qo'llanmada domen `chinor.uz`, server subdomeni `kassa.chinor.uz` deb
faraz qilinadi — o'zingiznikiga almashtiring.

---

## 2-QADAM — Named tunnel yaratish (Mac, bir martalik)

```bash
# 1) Cloudflare hisobiga kirish (brauzer ochiladi, domenni tanlaysiz)
cloudflared tunnel login

# 2) Tunnel yaratish (UUID + ~/.cloudflared/<UUID>.json credential fayli chiqadi)
cloudflared tunnel create chinor-pos

# 3) DNS yozuvini ulash: kassa.chinor.uz -> shu tunnel
cloudflared tunnel route dns chinor-pos kassa.chinor.uz
```

Keyin **`~/.cloudflared/config.yml`** faylini yarating:

```yaml
tunnel: chinor-pos
credentials-file: /Users/prom1/.cloudflared/<UUID>.json   # create chiqargan yo'l

ingress:
  - hostname: kassa.chinor.uz
    service: http://localhost:8765
  - service: http_status:404
```

Sinab ko'rish (oldindan `python main.py` ishlab turishi kerak):

```bash
cloudflared tunnel run chinor-pos
# boshqa terminalda:
curl -s https://kassa.chinor.uz/api/health     # -> {"ok": true}
```

### Doimiy xizmat qilib o'rnatish (qayta yoqilganda o'zi ishga tushadi)

```bash
sudo cloudflared service install
sudo launchctl start com.cloudflare.cloudflared    # yoki Mac'ni qayta yoqing
```

> Endi `ngrok-skip-browser-warning` header KERAK EMAS — Cloudflare'da oraliq
> ogohlantirish sahifasi yo'q. Eski header'lar zararsiz, qolsa ham ishlайди.

---

## 3-QADAM — Kodda URL'ni almashtirish

`unnatural-vibes-praying.ngrok-free.dev` → `kassa.chinor.uz` quyidagi joylarda:

| Fayl | Nimasi |
|---|---|
| `.env` | `MINI_APP_URL` ichidagi `?api=…` va `CORS_ALLOW_ORIGIN` |
| `desktop/package.json` | `build.publish[0].url` (Windows update feed — **build'ga muhrlanadi**) |
| `fronted/app.js` | `_DEFAULT_API` (zaxira qiymat) |

Buni qo'lda emas, skript bilan qiling (loyiha ildizida):

```bash
./set-server-host.sh kassa.chinor.uz
```

So'ng:
1. **Bot'ni qayta ishga tushiring:** `pkill -f 'python.*main.py'; python main.py`
2. **Frontend'ni Pages'ga qayta deploy qiling** (`fronted/` → `fronted-bgq.pages.dev`).
3. **Windows installer'ni qayta yig'ing:** `cd desktop && npm run dist`,
   so'ng yangi `.exe` + `latest.yml` ni `updates/` papkaga qo'ying (`release.sh`).

---

## 4-QADAM — Allaqachon o'rnatilgan Windows kassalar (muhim nuance!)

electron-updater feed URL **build vaqtida** `.exe` ichiga muhrlanadi. Demak
hozir tarqatilgan Windows ilovalar hali **eski ngrok feed**ini tekshiradi.
Ularni Cloudflare'ga ko'chirishning ikki yo'li:

- **A) Yumshoq ko'chish:** eski ngrok URL'ni yana biroz ishlatib turing va undan
  **bitta oxirgi yangilanish** chiqaring — uning ichida yangi (`kassa.chinor.uz`)
  feed muhrlangan bo'ladi. Kassalar o'sha update'ni olgach, keyingilarini
  Cloudflare'dan oladi. (ngrok endi ishlamasa bu yo'l yopiq.)
- **B) Qo'lda:** har bir Windows mashinaga yangi installer'ni bir marta qo'lda
  o'rnatib chiqing. Shundan keyin avtomatik yangilanish Cloudflare'dan ishlaydi.

**Sync va macOS** uchun bunday muammo yo'q — ular `serverUrl`ni ilova
sozlamasidan oladi: kassada **Sozlamalar → Server manzili**ni
`https://kassa.chinor.uz` ga o'zgartirish kifoya.

---

## Xavfsizlik eslatmasi

`.claude/settings.local.json` ichida ilgari Cloudflare API tokenlari bor edi
(`cfort_…`, `cfoat_…`, `cfk_…`). Ular fayldan olib tashlandi, lekin git
tarixida qolgan — **dash.cloudflare.com → My Profile → API Tokens** dan
ularni **bekor qilib (revoke), yangisini oling**.
