'use strict';
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const crypto = require('crypto');
const os = require('os');
const fs = require('fs');
const { spawn } = require('child_process');
const { autoUpdater } = require('electron-updater');
const api = require('./lib/api');
const { Store } = require('./lib/store');

// Logo (chek chop etish uchun base64)
let LOGO_DATAURL = '';
try {
  LOGO_DATAURL = 'data:image/png;base64,' +
    fs.readFileSync(path.join(__dirname, 'renderer', 'logo.png')).toString('base64');
} catch (_) {}

// Qurilma nomi (cheklarda "qaysi qurilmadan urilgani" ko'rinadi)
const PLATFORM = process.platform === 'win32' ? 'Windows'
  : process.platform === 'darwin' ? 'Mac' : process.platform;
const DEVICE = `${os.hostname()} · ${PLATFORM}`;

// Offlayn login uchun login+parol xeshi (qurilmaga bog'langan)
function credHash(login, password) {
  return crypto.createHash('sha256')
    .update((store && store.data.deviceId || '') + '\0' + login + '\0' + password)
    .digest('hex');
}

let win = null;
let store = null;

// Joriy holat — UI bilan baham ko'riladi
const status = {
  online: false,
  syncing: false,
  lastSync: '',
  lastError: '',
  update: { available: false, version: '', downloading: false, downloaded: false, progress: 0 },
};

// ── Holatni UI ga uzatish ─────────────────────────────────────────────
function snapshot() {
  return {
    loggedIn: store.loggedIn,
    cashier: store.data.cashier,
    serverUrl: store.data.serverUrl,
    kassaNo: store.data.kassaNo || 1,
    usdRate: store.data.usdRate,
    catalogCount: store.data.catalog.length,
    clientCount: (store.data.clients || []).length,
    draftCount: (store.data.drafts || []).length,
    catalogSyncedAt: store.data.catalogSyncedAt,
    pendingCount: store.pendingCount(),
    online: status.online,
    syncing: status.syncing,
    lastSync: status.lastSync,
    lastError: status.lastError,
    update: status.update,
    appVersion: app.getVersion(),
    todayTotal: store.todayTotal(),
    todayCount: store.todaySales().length,
    shift: shiftBrief(),
    locked: store.isLocked(),
    hasCashiers: store.hasCashiers(),
    cashiers: store.listCashiers(),
  };
}

// UI uchun smena holati (qisqa) — to'liq hisobot alohida IPC orqali
function shiftBrief() {
  const sh = store.getShift();
  if (!sh) return { open: false };
  const z = store.shiftSummary(sh);
  return {
    open: true, no: sh.no, openedAt: sh.openedAt,
    openedBy: (sh.openedBy && sh.openedBy.name) || '',
    openCash: z ? z.openCash : 0,
    expectedCash: z ? z.expectedCash : 0,
    salesCount: z ? z.sales.count : 0,
    salesTotal: z ? z.sales.total : 0,
    returnsCount: z ? z.returns.count : 0,
  };
}
function pushState() {
  if (win && !win.isDestroyed()) win.webContents.send('state:update', snapshot());
}

// ── Sinxronizatsiya ───────────────────────────────────────────────────
async function pushPendingSales() {
  const pending = store.pendingSales();
  if (!pending.length) return { pushed: 0 };
  const payload = pending.map((s) => ({
    local_id: s.local_id,
    // Qaytarishda lokal miqdor manfiy — serverga musbat (qaytarilgan dona) yuboriladi
    items: s.items.map((it) => ({
      product_id: it.product_id,
      qty: s.isReturn ? Math.abs(it.qty) : it.qty,
      price_sum: it.price_sum,
    })),
    payment: s.isReturn ? (s.returnMethod || s.returnPayment || 'cash') : s.payment,
    total_sum: s.isReturn ? Math.abs(s.totalSum) : s.totalSum,
    client_id: s.clientId || 0,
    is_nasiya: !!s.isNasiya,
    is_internal: !!s.isInternal,
    is_return: !!s.isReturn,
    return_method: s.isReturn ? (s.returnMethod || s.returnPayment || 'cash') : '',
    orig_receipt_no: s.origReceiptNo || '',
    created_at: s.created_at,
    source: DEVICE,
    receipt_no: s.receipt_no || '',
  }));
  const { ok, data } = await api.pushSales(store.data.serverUrl, store.data.token, payload);
  if (!ok || !data.ok) {
    status.lastError = (data && data.error) || 'Sotuvlarni yuborib bo\'lmadi';
    return { pushed: 0, error: status.lastError };
  }
  let pushed = 0;
  for (const r of data.results || []) {
    if (r.ok) { store.markSynced(r.local_id, r.server_id); pushed++; }
  }
  store.save();
  return { pushed };
}

async function pullCatalog() {
  const { ok, data } = await api.catalog(store.data.serverUrl, store.data.token);
  if (!ok || !data.ok) {
    status.lastError = (data && data.error) || 'Katalogni yuklab bo\'lmadi';
    return { pulled: 0, error: status.lastError };
  }
  store.setCatalog(data.products || [], data.usd_rate, data.clients || []);
  return { pulled: (data.products || []).length };
}

// Cheklarni inkremental tarzda yuklab, lokal keshga birlashtiramiz —
// shunda har qurilmada barcha qurilmalar cheklari vaqt bo'yicha ko'rinadi.
async function pullReceipts() {
  // keshda hech narsa yo'q bo'lsa — oxirgi 90 kunni so'raymiz; bo'lsa faqat yangisini
  let since = store.data.receiptsLastTs || '';
  if (!since) {
    const d = new Date(Date.now() - 90 * 24 * 3600 * 1000 + 5 * 3600 * 1000);
    since = d.toISOString().slice(0, 19).replace('T', ' ');
  }
  const { ok, data } = await api.recentSales(store.data.serverUrl, store.data.token, since);
  if (!ok || !data.ok) return { pulled: 0 };
  store.mergeReceipts(data.sales || []);
  return { pulled: (data.sales || []).length };
}

async function doSync({ force = false } = {}) {
  if (!store.loggedIn || status.syncing) return snapshot();
  status.syncing = true;
  pushState();
  try {
    const h = await api.health(store.data.serverUrl);
    status.online = !!h.ok;
    if (!status.online) {
      status.lastError = 'Internet/server bilan aloqa yo\'q';
      return snapshot();
    }
    status.lastError = '';
    // 1) Avval yubormagan sotuvlarni serverga jo'natamiz
    await pushPendingSales();
    // 2) Katalogni yangilaymiz (eskirgan bo'lsa yoki majburiy)
    if (force || store.catalogStaleMinutes() > 5 || store.data.catalog.length === 0) {
      await pullCatalog();
    }
    // 3) Cheklarni (barcha qurilmalardan) inkremental yuklab keshga birlashtiramiz
    try { await pullReceipts(); } catch (_) {}
    status.lastSync = new Date().toISOString();
  } catch (e) {
    status.lastError = String(e.message || e);
  } finally {
    status.syncing = false;
    pushState();
  }
  return snapshot();
}

// Tirik tekshiruv (online indikatori) — sotuv navbati bo'lsa sinxron ham qiladi
async function tick() {
  if (!store.loggedIn) return;
  await doSync();
}

// ── IPC handlerlari ────────────────────────────────────────────────────
function wireIpc() {
  ipcMain.handle('state:get', () => snapshot());

  ipcMain.handle('settings:server', (_e, url) => {
    store.setServerUrl(url);
    return snapshot();
  });

  ipcMain.handle('settings:kassa', (_e, n) => {
    store.setKassaNo(n);
    pushState();
    return snapshot();
  });

  ipcMain.handle('auth:login', async (_e, { login, password }) => {
    const base = store.data.serverUrl;
    const h = await api.health(base);
    status.online = !!h.ok;
    if (status.online) {
      const { ok, data } = await api.login(base, login, password);
      if (!ok || !data.ok) {
        return { ok: false, error: (data && data.error) || 'Kirib bo\'lmadi' };
      }
      store.setSession(data.session_token, data.cashier, data.usd_rate);
      store.setAuthHash(credHash(login, password));   // offlayn kirish uchun eslab qolamiz
      await doSync({ force: true });
      return { ok: true, state: snapshot() };
    }
    // Offlayn: avval internetli kirgan bo'lsa va parol mos kelsa — kiritamiz
    if (store.data.authHash && credHash(login, password) === store.data.authHash
        && store.data.token && store.data.cashier) {
      store.restoreSession();
      pushState();
      return { ok: true, offline: true, state: snapshot() };
    }
    return {
      ok: false,
      error: store.data.authHash
        ? 'Login yoki parol noto\'g\'ri (offlayn tekshiruv)'
        : 'Internet yo\'q. Birinchi marta internetli kiring, keyin offlayn ishlaydi.',
    };
  });

  ipcMain.handle('auth:logout', async () => {
    // Onlayn bo'lsa, qolgan sotuvlarni yuborishga harakat qilamiz
    if (store.loggedIn && store.pendingCount() && status.online) {
      try { await pushPendingSales(); } catch {}
    }
    store.logout();   // yumshoq chiqish — offlayn qayta kirish mumkin bo'ladi
    pushState();
    return snapshot();
  });

  ipcMain.handle('catalog:get', () => store.data.catalog);
  // Kassada mahsulotga shtrix-kod biriktirish (lokal, offlayn — qurilmada saqlanadi)
  ipcMain.handle('product:setBarcode', (_e, { productId, barcode }) =>
    store.setProductBarcode(productId, barcode));
  ipcMain.handle('clients:get', () => store.data.clients || []);

  ipcMain.handle('drafts:get', () => store.listDrafts());
  ipcMain.handle('drafts:save', (_e, draft) => {
    const d = store.saveDraft(draft || {});
    pushState();
    return d;
  });
  ipcMain.handle('drafts:remove', (_e, id) => {
    store.removeDraft(id);
    pushState();
    return store.listDrafts();
  });

  ipcMain.handle('tabs:get', () => store.getOpenChecks());
  ipcMain.handle('tabs:set', (_e, list) => { store.setOpenChecks(list); return true; });

  ipcMain.handle('assets:logo', () => LOGO_DATAURL);

  // Printerlar ro'yxati + belgilangan printer
  ipcMain.handle('printers:list', async () => {
    let printers = [];
    try {
      if (win && !win.isDestroyed()) printers = await win.webContents.getPrintersAsync();
    } catch (_) {}
    return {
      printers: printers.map((p) => ({ name: p.name, display: p.displayName || p.name, isDefault: !!p.isDefault })),
      current: store.data.printerName || '',
    };
  });
  ipcMain.handle('printer:set', (_e, name) => {
    store.data.printerName = name || '';
    store.save();
    return true;
  });

  // Chek chop etish — JIM (belgilangan/standart printerga, dialog so'ramasdan)
  ipcMain.handle('receipt:print', async (_e, html) => {
    return new Promise((resolve) => {
      // sandbox:false — Windows printer drayveriga to'liq kirish uchun.
      // Aniq enini beramiz (80mm ≈ 302px) — kontent balandligi barqaror o'lchanadi.
      let pw = new BrowserWindow({ show: false, width: 320, height: 1200, useContentSize: true,
        webPreferences: { sandbox: false } });
      const doPrint = (heightMicrons) => {
        const opts = {
          silent: true,
          printBackground: true,
          margins: { marginType: 'none' },
          // 80mm termal qog'oz. Balandlik = chekning aniq balandligi (mikronlarda)
          // → bitta sahifa: o'rtasidan kesilmaydi va ortiqcha qog'oz ketmaydi.
          pageSize: { width: 80000, height: heightMicrons },
        };
        if (store.data.printerName) opts.deviceName = store.data.printerName;
        pw.webContents.print(opts, (ok, reason) => {
          try { pw.close(); } catch (_) {}
          pw = null;
          resolve({ ok, reason });
        });
      };
      pw.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html)).then(async () => {
        // Logo/QR rasmlari to'liq yuklanguncha kutamiz, so'ng chekning aniq balandligini o'lchaymiz
        let heightPx = 0;
        try {
          heightPx = await pw.webContents.executeJavaScript(`new Promise((res) => {
            const imgs = Array.from(document.images || []);
            // body kontent balandligi (viewport bilan shishmaydi, chunki body balandligi avto)
            const measure = () => res(Math.ceil(Math.max(
              document.body.scrollHeight, document.body.getBoundingClientRect().height)));
            let left = imgs.filter((i) => !i.complete).length;
            if (!left) return measure();
            const tick = () => { if (--left <= 0) measure(); };
            imgs.forEach((i) => { if (!i.complete) { i.addEventListener('load', tick); i.addEventListener('error', tick); } });
            setTimeout(measure, 5000);
          })`);
        } catch (_) {}
        heightPx = Number(heightPx) || 0;
        // O'lchov ishonchsiz bo'lsa (juda kichik/katta) — xavfsiz zaxira balandlik
        if (heightPx < 200 || heightPx > 8000) heightPx = 1500;
        // CSS px → mikron (1 px = 1/96 dyuym = 25400/96 mikron).
        // Zaxira: kamida 24mm yoki 10% — chop etishda matn ekrandagidan biroz balandroq
        // chiqishi mumkin; sahifa kontentdan kaltaroq bo'lsa printer o'rtadan kesadi.
        const contentMicrons = Math.round(heightPx * (25400 / 96));
        const buffer = Math.max(24000, Math.round(contentMicrons * 0.10));
        const heightMicrons = Math.min(1500000, contentMicrons + buffer);
        // Windows printer drayveri sekin bo'lishi mumkin — chop etishdan oldin kutamiz
        setTimeout(() => doPrint(heightMicrons), 800);
      }).catch((e) => {
        try { pw.close(); } catch (_) {}
        resolve({ ok: false, reason: String(e) });
      });
    });
  });

  // Telegram bot orqali to'lov linkini olish (getUpdates polling) va SMS yuborish.
  // Token KODDA saqlanmaydi: avval PAYMENT_BOT_TOKEN muhit o'zgaruvchisi, bo'lmasa
  // loyiha ildizidagi gitignored .env faylidagi PAYMENT_BOT_TOKEN dan o'qiladi (lokal test).
  let BOT_TOKEN = process.env.PAYMENT_BOT_TOKEN || '';
  if (!BOT_TOKEN) {
    try {
      const _env = fs.readFileSync(path.join(__dirname, '..', '.env'), 'utf8');
      const _m = _env.match(/^\s*PAYMENT_BOT_TOKEN\s*=\s*(.+?)\s*$/m);
      if (_m) BOT_TOKEN = _m[1].replace(/^["']|["']$/g, '');
    } catch (_) {}
  }
  const MY_TELEGRAM_ID = 6787907623;
  let lastUpdateId = 0;
  let paymentSince = 0;   // faqat shu vaqtdan (Unix sek.) keyin kelgan xabarlar hisobga olinadi

  // Xabardan haqiqiy URL (to'lov linki) ni ajratib olamiz.
  // Oddiy matn yoki eski yozuv link sifatida qabul qilinmaydi.
  function extractPaymentLink(text) {
    const m = String(text || '').match(/https?:\/\/\S+/i);
    return m ? m[0] : '';
  }

  // Telegram navbatidagi eski/o'qilmagan xabarlarni "tasdiqlab" tashlaymiz —
  // shunda yangi to'lov uchun faqat so'rovdan KEYIN kelgan xabar hisobga olinadi.
  async function drainBotUpdates() {
    for (let i = 0; i < 6; i++) {
      try {
        const url = `https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?offset=${lastUpdateId + 1}&timeout=0`;
        const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
        const data = await res.json();
        if (!data.ok || !data.result || !data.result.length) break;
        for (const upd of data.result) if (upd.update_id > lastUpdateId) lastUpdateId = upd.update_id;
        if (data.result.length < 100) break;
      } catch (_) { break; }
    }
  }

  async function botSend(text) {
    try {
      const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`;
      await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: MY_TELEGRAM_ID, text }),
        signal: AbortSignal.timeout(5000),
      });
    } catch (_) {}
  }

  ipcMain.handle('payment:sendmsg', async (_e, text) => {
    await botSend(text);
    return true;
  });

  // Yangi to'lov boshlanishidan oldin: eski xabarlarni tozalab, "shu vaqtdan keyin"
  // belgisini qo'yamiz. Shunda eski linkka QR generatsiya qilinmaydi.
  // DIQQAT: bu yerda ALOHIDA to'lov boti (BOT_TOKEN, yuqorida) ishlatiladi —
  // u serverning asosiy boti bilan to'qnashmaydi. Link to'g'ridan-to'g'ri shu
  // bot orqali olinadi (server/ngrok kerak emas).
  ipcMain.handle('payment:begin', async () => {
    await drainBotUpdates();
    paymentSince = Math.floor(Date.now() / 1000);
    return true;
  });

  ipcMain.handle('payment:poll', async () => {
    try {
      const url = `https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?offset=${lastUpdateId + 1}&timeout=10`;
      const res = await fetch(url, { signal: AbortSignal.timeout(12000) });
      const data = await res.json();
      if (!data.ok || !data.result || !data.result.length) return { ok: false };
      let link = '';
      for (const upd of data.result) {
        if (upd.update_id > lastUpdateId) lastUpdateId = upd.update_id;
        const msg = upd.message || upd.channel_post || {};
        // Faqat: o'zimdan + to'lov so'rovidan KEYIN kelgan + ichida haqiqiy URL bo'lgan xabar
        if (msg.from && msg.from.id === MY_TELEGRAM_ID && msg.text
            && msg.date && msg.date >= paymentSince) {
          const cand = extractPaymentLink(msg.text);
          if (cand) link = cand;
        }
      }
      if (!link) return { ok: false };
      return { ok: true, link };
    } catch (e) {
      return { ok: false, error: String(e.message || e) };
    }
  });

  // Yangilanish — Windows: electron-updater; macOS: o'zimizning updater (imzosiz)
  ipcMain.handle('update:check', () => {
    if (process.platform === 'darwin') { macCheckForUpdates(); return true; }
    autoUpdater.checkForUpdates().catch(() => {});
    return true;
  });
  ipcMain.handle('update:download', () => {
    if (process.platform === 'darwin') { macDownloadUpdate(); return true; }
    status.update.downloading = true; pushState();
    autoUpdater.downloadUpdate().catch((e) => {
      status.update.downloading = false; status.lastError = 'Yangilanishni yuklab bo\'lmadi'; pushState();
    });
    return true;
  });
  // Jim o'rnatish (oynasiz) + avtomatik qayta ochish
  ipcMain.handle('update:install', () => {
    if (process.platform === 'darwin') { macInstallUpdate(); return true; }
    try { autoUpdater.quitAndInstall(true, true); } catch (_) {} return true;
  });

  ipcMain.handle('sale:record', (_e, sale) => {
    if (!store.getShift() || store.isLocked())   // smena qo'lda ochilishi shart
      return { ok: false, error: 'Smena yopiq — avval smenani oching' };
    const saved = store.addSale(sale);
    pushState();
    // Onlayn bo'lsa darhol yuborishga urinamiz (kutmasdan)
    if (status.online) doSync().catch(() => {});
    return { ok: true, sale: saved, state: snapshot() };
  });

  // Sotuvni (qisman/to'liq) qaytarish — qoldiq tiklanadi, serverga sinxronlanadi
  ipcMain.handle('sale:return', (_e, ret) => {
    if (!ret || !Array.isArray(ret.items) || !ret.items.length) {
      return { ok: false, error: 'Qaytariladigan mahsulot yo\'q' };
    }
    if (!store.getShift() || store.isLocked())
      return { ok: false, error: 'Smena yopiq — avval smenani oching' };
    const saved = store.addReturn(ret);
    pushState();
    if (status.online) doSync().catch(() => {});
    return { ok: true, ret: saved, state: snapshot() };
  });

  // ── Kassirlar (lokal) + kassa qulfi ────────────────────────────────
  ipcMain.handle('cashiers:list', () => store.listCashiers());
  ipcMain.handle('cashier:add', (_e, { name, pin, authPin }) => {
    if (store.hasCashiers() && !store.verifyPin(authPin))
      return { ok: false, error: 'Tasdiqlash paroli noto\'g\'ri (mavjud kassir paroli kerak)' };
    const r = store.addCashier(name, pin);
    pushState();
    return r;
  });
  ipcMain.handle('cashier:remove', (_e, { id, authPin }) => {
    if (!store.verifyPin(authPin)) return { ok: false, error: 'Tasdiqlash paroli noto\'g\'ri' };
    const r = store.removeCashier(id);
    pushState();
    return r;
  });
  // Lock (Win-lock) — kassir naqdni sanab tasdiqlaydi (1-tekshiruv)
  ipcMain.handle('kassa:lock', (_e, opts) => {
    store.lockKassa(opts || {});
    pushState();
    return { ok: true, state: snapshot() };
  });
  // Qulfni ochish — parolga qarab: davom etish / smena ochish / topshirish
  ipcMain.handle('kassa:unlock', (_e, { pin }) => {
    const c = store.verifyPin(pin);
    if (!c) return { ok: false, error: 'Parol noto\'g\'ri' };
    const sh = store.getShift();
    if (!sh) return { ok: true, action: 'openShift', cashier: c };
    const owner = sh.openedBy || {};
    if (owner.id === c.id || !owner.id) {
      store.resume(c); pushState();
      return { ok: true, action: 'resume', cashier: c, state: snapshot() };
    }
    const z = store.shiftSummary(sh);
    return {
      ok: true, action: 'handover', cashier: c,
      prevCashier: owner.name || '',
      expected: z ? z.expectedCash : 0,
      lockCount: sh.lockCount || null,
    };
  });
  // Smenani qo'lda ochish (boshlang'ich naqd)
  ipcMain.handle('shift:openManual', (_e, { pin, openCash }) => {
    const c = store.verifyPin(pin);
    if (!c) return { ok: false, error: 'Parol noto\'g\'ri' };
    store.openShiftFor(c, openCash);
    pushState();
    return { ok: true, state: snapshot() };
  });
  // Topshirish — yangi kassir sanab oladi (2-tekshiruv) → eski Z + yangi smena
  ipcMain.handle('kassa:handover', (_e, { pin, counted }) => {
    const c = store.verifyPin(pin);
    if (!c) return { ok: false, error: 'Parol noto\'g\'ri' };
    const r = store.handover(c, counted);
    pushState();
    return { ok: true, z: r.z, state: snapshot() };
  });

  // ── Smena (shift) ──────────────────────────────────────────────────
  ipcMain.handle('shift:get', () => store.getShift());
  ipcMain.handle('shift:open', (_e, opts) => {
    const sh = store.openShift({ openCash: (opts && opts.openCash) || 0, cashier: store.data.cashier });
    pushState();
    return { ok: true, shift: sh, state: snapshot() };
  });
  ipcMain.handle('shift:summary', () => store.shiftSummary());
  ipcMain.handle('shift:cashmove', (_e, mv) => {
    const m = store.addCashMove(mv || {});
    pushState();
    return { ok: !!m, move: m, summary: store.shiftSummary(), state: snapshot() };
  });
  ipcMain.handle('shift:close', () => {
    const z = store.closeShift();
    store.data.locked = true; store.save();   // smena yopildi → kassa qulflandi (keyingi smena uchun)
    pushState();
    return { ok: !!z, summary: z, state: snapshot() };
  });
  ipcMain.handle('sale:updateQr', (_e, { local_id, qrLink }) => {
    const s = store.data.sales.find((x) => x.local_id === local_id);
    if (s) { s.qrLink = qrLink || ''; store.save(); }
    return true;
  });

  ipcMain.handle('sync:now', async () => {
    await doSync({ force: true });
    return snapshot();
  });

  ipcMain.handle('sales:today', () => ({
    sales: store.todaySales(),
    total: store.todayTotal(),
  }));

  // Hamma cheklar — qurilmalararo sinxron kesh + hali yuborilmagan lokal
  // sotuvlar, vaqt bo'yicha tartiblangan. Online bo'lsa avval keshni yangilaymiz.
  ipcMain.handle('sales:recent', async () => {
    if (status.online && store.data.token) {
      try { await pullReceipts(); } catch (_) {}
    }
    return {
      source: status.online ? 'server' : 'local',
      sales: store.getReceiptsMerged(),
    };
  });
}

// ── Avtomatik yangilanish ───────────────────────────────────────────────
function setupUpdater() {
  autoUpdater.autoDownload = true;             // yangilanishni avtomatik yuklab oladi
  autoUpdater.autoInstallOnAppQuit = true;
  // ngrok-free ogohlantirish sahifasini chetlab o'tish
  autoUpdater.requestHeaders = { 'ngrok-skip-browser-warning': 'true' };

  autoUpdater.on('update-available', (info) => {
    status.update = { available: true, version: info.version, downloading: false, downloaded: false, progress: 0 };
    pushState();
  });
  autoUpdater.on('update-not-available', () => {
    status.update = { available: false, version: '', downloading: false, downloaded: false, progress: 0 };
    pushState();
  });
  autoUpdater.on('download-progress', (p) => {
    status.update.downloading = true;
    status.update.progress = Math.round(p.percent || 0);
    pushState();
  });
  autoUpdater.on('update-downloaded', (info) => {
    status.update.downloading = false;
    status.update.downloaded = true;
    status.update.version = info.version;
    pushState();
  });
  autoUpdater.on('error', (e) => { console.error('[updater]', e && e.message); });

  if (app.isPackaged && process.platform === 'win32') {
    setTimeout(() => autoUpdater.checkForUpdates().catch(() => {}), 8000);
    setInterval(() => autoUpdater.checkForUpdates().catch(() => {}), 30 * 60 * 1000);
  } else if (app.isPackaged && process.platform === 'darwin') {
    setTimeout(() => macCheckForUpdates(), 8000);
    setInterval(() => macCheckForUpdates(), 30 * 60 * 1000);
  }
}

// ── macOS yangilanishi (imzosiz, o'zini-o'zi almashtiradi) ──────────────────
// macOS'da electron-updater (Squirrel.Mac) ilovaning imzolangan bo'lishini talab
// qiladi. Bizning ilova imzosiz bo'lgani uchun yengil updater: serverdagi
// latest-mac.yml ni tekshiramiz, .zip ni yuklab olamiz, .app ni almashtirib
// ilovani qayta ochamiz. UI (status.update) Windows bilan bir xil ishlatiladi.
let macUpdate = null;   // { version, file, zipPath }

function verNewer(a, b) {
  const pa = String(a).split('.').map((n) => parseInt(n, 10) || 0);
  const pb = String(b).split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] || 0, y = pb[i] || 0;
    if (x > y) return true;
    if (x < y) return false;
  }
  return false;
}

function updatesBase() {
  return String((store && store.data.serverUrl) || '').replace(/\/+$/, '') + '/updates';
}

async function macCheckForUpdates() {
  try {
    const res = await fetch(`${updatesBase()}/latest-mac.yml`, {
      headers: { 'ngrok-skip-browser-warning': 'true' },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return;
    const text = await res.text();
    const ver = (text.match(/version:\s*['"]?([0-9][0-9.]*)/) || [])[1];
    const file = (text.match(/url:\s*([^\s'"]+\.zip)/) || [])[1];
    if (!ver || !file) return;
    if (verNewer(ver, app.getVersion())) {
      macUpdate = { version: ver, file };
      status.update = { available: true, version: ver, downloading: false, downloaded: false, progress: 0 };
    } else {
      macUpdate = null;
      status.update = { available: false, version: '', downloading: false, downloaded: false, progress: 0 };
    }
    pushState();
  } catch (_) {}
}

async function macDownloadUpdate() {
  if (!macUpdate) return;
  status.update.downloading = true; status.update.progress = 0; pushState();
  try {
    const fileUrl = /^https?:/i.test(macUpdate.file)
      ? macUpdate.file
      : `${updatesBase()}/${macUpdate.file}`;
    const res = await fetch(fileUrl, {
      headers: { 'ngrok-skip-browser-warning': 'true' },
      signal: AbortSignal.timeout(180000),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const buf = Buffer.from(await res.arrayBuffer());
    const dir = path.join(app.getPath('temp'), 'chinor-update');
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
    const zipPath = path.join(dir, 'update.zip');
    fs.writeFileSync(zipPath, buf);
    macUpdate.zipPath = zipPath;
    status.update.downloading = false;
    status.update.downloaded = true;
    status.update.progress = 100;
    pushState();
  } catch (e) {
    status.update.downloading = false;
    status.lastError = "Yangilanishni yuklab bo'lmadi";
    pushState();
  }
}

function macInstallUpdate() {
  if (!macUpdate || !macUpdate.zipPath) return;
  const exe = app.getPath('exe');               // .../Chinor Kassa.app/Contents/MacOS/Chinor Kassa
  const appBundle = exe.split('/Contents/')[0];  // .../Chinor Kassa.app
  const dir = path.dirname(macUpdate.zipPath);
  const extract = path.join(dir, 'extracted');
  const q = (s) => s.replace(/"/g, '\\"');
  const script = `#!/bin/bash
APP="${q(appBundle)}"
ZIP="${q(macUpdate.zipPath)}"
EXTRACT="${q(extract)}"
# ilova to'liq yopilguncha kutamiz (maks ~15s)
for i in $(seq 1 50); do kill -0 ${process.pid} 2>/dev/null || break; sleep 0.3; done
rm -rf "$EXTRACT"; mkdir -p "$EXTRACT"
/usr/bin/ditto -x -k "$ZIP" "$EXTRACT" || exit 1
NEW=$(/usr/bin/find "$EXTRACT" -maxdepth 2 -name "*.app" -type d | head -1)
if [ -n "$NEW" ]; then
  rm -rf "$APP"
  /usr/bin/ditto "$NEW" "$APP"
  /usr/bin/xattr -cr "$APP" 2>/dev/null || true
fi
sleep 0.5
open "$APP"
`;
  const scriptPath = path.join(dir, 'install.sh');
  fs.writeFileSync(scriptPath, script, { mode: 0o755 });
  const child = spawn('/bin/bash', [scriptPath], { detached: true, stdio: 'ignore' });
  child.unref();
  setTimeout(() => { try { app.quit(); } catch (_) {} }, 300);
}

// ── Oyna ────────────────────────────────────────────────────────────────
function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 640,
    backgroundColor: '#0f1f17',
    title: 'Chinor Kassa',
    icon: path.join(__dirname, 'renderer', 'logo.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.removeMenu();
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.webContents.on('did-finish-load', () => pushState());
}

app.whenReady().then(() => {
  const dataFile = path.join(app.getPath('userData'), 'chinor-kassa-data.json');
  store = new Store(dataFile);
  store.migrateLockState();   // yangilanish: lokal kassir yo'q bo'lsa eski avto-smenani tashlaymiz, locked=true
  wireIpc();
  createWindow();
  setupUpdater();
  // Har 12 soniyada tirik tekshiruv + sinxron
  setInterval(tick, 12000);
  setTimeout(tick, 1500);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
