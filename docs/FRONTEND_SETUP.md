# Frontend ishga tushirish — NGROK va Static Server Setup

> ⚠️ **ESKIRGAN:** ngrok pullik bo'lgani uchun endi **Cloudflare Tunnel**
> ishlatiladi. To'liq qo'llanma: [CLOUDFLARE_TUNNEL.md](CLOUDFLARE_TUNNEL.md).
> Quyidagi ngrok qadamlari faqat tarix uchun qoldirilgan — `ngrok http 8765`
> o'rniga doimiy URL uchun named tunnel sozlang.

Frontend (Mini App) ishga tushirish uchun **3 ta terminalda** 3 ta komanda jamiylisi kerak:

---

## 🚀 Terminal 1: Bot (API Server)

Bot allaqachon ishga tushgan bo'lsa, bu tayyor:
```bash
# Agar ishga tushirmagan bo'lsangiz:
cd /Users/prom1/Documents/pos_v2_fixed_new
python main.py
```

**Ko'rish kerak**:
```
✨━━━━━━━━━━━━━━━━━━━━━✨
🚀 POS BOT ISHGA TUSHDI!
...
INFO    ... API started at http://0.0.0.0:8765
```

---

## 🌐 Terminal 2: Frontend Static Server

Frontend'ni HTTP server orqali serve qilish:

### Option A: Python HTTP Server (eng sodda)
```bash
cd /Users/prom1/Documents/pos_v2_fixed_new/fronted
python3 -m http.server 3000
```

**Ko'rish kerak**:
```
Serving HTTP on 0.0.0.0 port 3000 (http://0.0.0.0:3000/) ...
```

### Option B: Node.js (agar o'rnatilgan bo'lsa)
```bash
cd /Users/prom1/Documents/pos_v2_fixed_new/fronted
npx serve -l 3000
```

---

## 🔗 Terminal 3: NGROK (Tunnel yaratish)

**API Server'ni expose qilish** (port 8765):

```bash
ngrok http 8765
```

**Ko'rish kerak**:
```
Session Status                online
Account                       Name
Version                       3.x.x
Region                        United States
Forwarding                    https://xxxx-xxxx-xxxx.ngrok.io -> http://localhost:8765
```

---

## 🎯 Step-by-Step Amal

### 1️⃣ Main.py Tekshiruvi (siz allaqachon qildingiz)
```bash
✅ main.py ishga tushgan
✅ API 8765 port'da
```

### 2️⃣ Frontend Server Ishga Tushirish
```bash
# Terminal 2'da:
cd /Users/prom1/Documents/pos_v2_fixed_new/fronted
python3 -m http.server 3000
```

### 3️⃣ NGROK Tunnel Yaratish
```bash
# Terminal 3'da:
ngrok http 8765
```
**NGROK URL'ni nusxalang** (masalan: `https://1234-5678-abcd.ngrok.io`)

### 4️⃣ .env'ni Yangilash
```bash
# Terminal 4'da yoki text editor'da .env faylini tahrirlash:
MINI_APP_URL=https://1234-5678-abcd.ngrok.io/fronted/index.html
```

---

## 📋 COMPLETE Qo'llanma

Agar siz hammasi boshlangandan:

### **Terminal 1** — Bot API:
```bash
cd /Users/prom1/Documents/pos_v2_fixed_new
python main.py
```

### **Terminal 2** — Frontend:
```bash
cd /Users/prom1/Documents/pos_v2_fixed_new/fronted
python3 -m http.server 3000
```

### **Terminal 3** — NGROK:
```bash
ngrok http 8765
```

### **Terminal 4** — Environment Update:
```bash
cd /Users/prom1/Documents/pos_v2_fixed_new

# 1. .env'da MINI_APP_URL'ni yangilash (ngrok URL'ini qo'shish)
# 2. CORS_ALLOW_ORIGIN'ni ham qo'shish:

# Misol .env:
MINI_APP_URL=https://1234-5678-abcd.ngrok.io/fronted/index.html
CORS_ALLOW_ORIGIN=https://1234-5678-abcd.ngrok.io
```

---

## 🧪 Tekshirish

Frontend ishga tushganligini tekshirish:

### 1. Frontend HTML'i qabul qilinganmi?
```bash
curl -s http://localhost:3000/index.html | head -10
```
**Ko'rish kerak**: `<!DOCTYPE html>`

### 2. API availability?
```bash
curl -s https://1234-5678-abcd.ngrok.io/api/health
```
**Ko'rish kerak**: `{"ok": true}`

### 3. CORS tekshirish?
```bash
curl -H "Origin: https://1234-5678-abcd.ngrok.io" \
  -H "X-Requested-With: XMLHttpRequest" \
  https://1234-5678-abcd.ngrok.io/api/health
```

---

## ⚠️ ESLATMALAR

### ngrok URL har qayta o'zgaradi!
```
❌ ESKI: https://abc-123.ngrok.io  (ishlamaydi)
✅ YANGI: https://xyz-789.ngrok.io (har safar yangi)
```
**Har safar ngrok qayta ishga tushtirilganda .env'da URL'ni yangilash kerak!**

### Alternativa: ngrok authtoken (stabil domain)
```bash
# ngrok account yaratish va token'ni setup qilish
ngrok config add-authtoken YOUR_TOKEN

# Keyingi marta ngrok ishga tushtirilganda URL stabil bo'ladi
ngrok http 8765 --domain=your-stable-domain.ngrok-free.app
```

---

## 🔥 QUICK START (Eng sodda)

```bash
# Terminal 1: Bot
python main.py

# Terminal 2: Frontend
cd fronted && python3 -m http.server 3000

# Terminal 3: NGROK (ngrok URL'ni nusxalang!)
ngrok http 8765

# Terminal 4: .env'ni yangilash (URL'ni qo'shish)
nano .env
# MINI_APP_URL=https://YOUR_NGROK_URL/fronted/index.html
# CORS_ALLOW_ORIGIN=https://YOUR_NGROK_URL
```

---

## 📱 Bot'da Mini App'ni Test Qilish

Telegram Bot'da `/start` tugmasini bosing, "🌐 Mini App" tugmasi paydo bo'ladi.

**Bot menyuda ko'rish kerak**:
```
🌐 Mini App  ← Bu tugma tekan
```

---

## 🆘 Debugging

**Frontend yo'qligini ko'rsatsa**:
```bash
# MINI_APP_URL .env'da noto'g'ri bo'lishi mumkin
grep MINI_APP_URL .env

# Frontend server'i ishga tushganligini tekshirish
curl http://localhost:3000/index.html
```

**API xatolarini ko'rsatsa**:
```bash
# API server'i ishga tushganligini tekshirish
curl http://localhost:8765/api/health

# ngrok tunnel'ini tekshirish
# Terminal 3'dagi logs'ni ko'rish
```

**CORS xatolarini ko'rsatsa**:
```bash
# .env'da CORS_ALLOW_ORIGIN'i tekshirish
grep CORS_ALLOW_ORIGIN .env

# Kerak bo'lsa ngrok URL'ni yangilash
```

