'use strict';
const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { execFile } = require('child_process');
const api = require('./lib/api');
const { Store } = require('./lib/store');

let win = null;
let store = null;

function dataFile() {
  return path.join(app.getPath('userData'), 'chinor-admin-data.json');
}

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 680,
    backgroundColor: '#f3f4f8',
    title: 'Chinor',
    icon: path.join(__dirname, 'renderer', 'icon_mac.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  // Tashqi havolalar tashqi brauzerda ochilsin
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

app.whenReady().then(() => {
  store = new Store(dataFile());
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// ── IPC: holat / sozlamalar ──────────────────────────────────────────────
ipcMain.handle('state:get', () => ({
  serverUrl: store.get('serverUrl'),
  token: store.get('token'),
  admin: store.get('admin'),
  usdRate: store.get('usdRate'),
  loggedOut: store.get('loggedOut'),
  labelPrinter: store.get('labelPrinter') || '',
  labelW: store.get('labelW') || 30,
  labelH: store.get('labelH') || 20,
}));

ipcMain.handle('settings:server', (_e, url) => store.setServerUrl(url));

// ── IPC: auth ─────────────────────────────────────────────────────────────
ipcMain.handle('auth:login', async (_e, { server, login, password }) => {
  if (server) store.setServerUrl(server);
  const base = store.get('serverUrl');
  const r = await api.login(base, login, password);
  if (!r.ok || !r.data.ok) {
    return { ok: false, error: (r.data && r.data.error) || 'Login yoki parol noto\'g\'ri' };
  }
  store.setSession({
    token: r.data.session_token,
    admin: r.data.cashier,
    usdRate: r.data.usd_rate,
  });
  return { ok: true, admin: r.data.cashier, usdRate: r.data.usd_rate };
});

ipcMain.handle('auth:logout', () => { store.clearSession(); return { ok: true }; });

// ── IPC: hisobot ma'lumotlari (server token bilan) ───────────────────────
function withAuth(fn) {
  return async (_e, arg) => {
    const base = store.get('serverUrl');
    const token = store.get('token');
    if (!token) return { ok: false, error: 'Avtorizatsiya talab qilinadi', authRequired: true };
    const r = await fn(base, token, arg);
    if (r.status === 401) return { ok: false, error: 'Sessiya tugadi', authRequired: true };
    if (!r.ok || (r.data && r.data.ok === false)) {
      return { ok: false, error: (r.data && r.data.error) || 'Server xatosi', status: r.status };
    }
    return Object.assign({ ok: true }, r.data);
  };
}

ipcMain.handle('data:stats', withAuth((base, token) => api.stats(base, token)));
ipcMain.handle('data:recentSales', withAuth((base, token, since) => api.recentSales(base, token, since || '')));
ipcMain.handle('data:orders', withAuth((base, token) => api.orders(base, token)));
ipcMain.handle('data:products', withAuth((base, token, page) => api.products(base, token, page || 0)));
ipcMain.handle('data:productQty', withAuth((base, token, arg) => api.productQty(base, token, arg.id, arg.delta)));
ipcMain.handle('data:productSave', withAuth((base, token, arg) => api.productSave(base, token, arg)));
ipcMain.handle('data:prixodScan', withAuth((base, token, arg) => api.prixodScan(base, token, arg.bytes, arg.filename)));
ipcMain.handle('data:prixodList', withAuth((base, token, arg) => api.prixodList(base, token, (arg && arg.from) || '', (arg && arg.to) || '')));
ipcMain.handle('data:prixodSave', withAuth((base, token, arg) => api.prixodSave(base, token, arg)));
ipcMain.handle('data:clients', withAuth((base, token) => api.clients(base, token)));
ipcMain.handle('data:admins', withAuth((base, token) => api.admins(base, token)));
ipcMain.handle('data:settings', withAuth((base, token) => api.settings(base, token)));

ipcMain.handle('net:health', async () => {
  const r = await api.health(store.get('serverUrl'));
  return { ok: r.ok && r.data && r.data.ok !== false };
});

// ── IPC: tizimga ulangan printerlar ro'yxati ─────────────────────────────
ipcMain.handle('printers:list', async () => {
  let printers = [];
  try {
    if (win && !win.isDestroyed()) printers = await win.webContents.getPrintersAsync();
  } catch (_) {}
  return printers.map((p) => ({
    name: p.name,
    display: p.displayName || p.name,
    isDefault: !!p.isDefault,
  }));
});

// ── IPC: sennik sozlamalarini saqlash (printer + yorliq o'lchami) ─────────
ipcMain.handle('settings:label', (_e, cfg) => {
  cfg = cfg || {};
  store.set('labelPrinter', String(cfg.printer || ''));
  if (cfg.w) store.set('labelW', Number(cfg.w) || 30);
  if (cfg.h) store.set('labelH', Number(cfg.h) || 20);
  return { ok: true };
});

// ── IPC: sennik / narx teglarini chop etish ──────────────────────────────
// payload: oddiy HTML satri (eski yo'l → tizim oynasi) YOKI obyekt:
//   { html, silent, deviceName, widthMicrons, heightMicrons }
// silent=true va deviceName berilsa — dialogsiz, to'g'ridan-to'g'ri shu
// printerga, aniq qog'oz o'lchami bilan (roll label printer uchun).
ipcMain.handle('print:html', async (_e, payload) => {
  const o = (typeof payload === 'string') ? { html: payload } : (payload || {});
  const html = String(o.html || '');
  return new Promise((resolve) => {
    let pw = new BrowserWindow({
      show: false, width: 900, height: 1200, useContentSize: true,
      webPreferences: { sandbox: false },
    });
    pw.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
      .then(async () => {
        // Barcode SVG / matn render bo'lguncha qisqa kutish
        await new Promise((r) => setTimeout(r, 350));
        const opts = { silent: !!o.silent, printBackground: true };
        if (o.silent) opts.margins = { marginType: 'none' };
        if (o.deviceName) opts.deviceName = o.deviceName;
        if (o.widthMicrons && o.heightMicrons) {
          opts.pageSize = { width: o.widthMicrons, height: o.heightMicrons };
        }
        pw.webContents.print(opts, (ok, reason) => {
          try { pw.close(); } catch (_) {}
          pw = null;
          resolve({ ok, reason });
        });
      })
      .catch((err) => {
        try { pw.close(); } catch (_) {}
        resolve({ ok: false, reason: String(err) });
      });
  });
});

// ── Sennik (narx yorlig'i) → TSPL (termal label printer tili) ─────────────
// XP-365B kabi label printerlar TSPL da gaplashadi; rasterli drayver ishlamaydi.
// Shuning uchun yorliqni TSPL matn buyruqlari bilan to'g'ridan-to'g'ri chizamiz:
// narx (tepada) + Code128 shtrix-kod (SKU, ostida raqami bilan).
function tsplSafe(s) {
  return String(s == null ? '' : s).replace(/["\\]/g, ' ').replace(/[\r\n]+/g, ' ').trim();
}

function buildTsplLabels(tags, cfg) {
  const Wmm = Math.max(10, Number(cfg.w) || 30);
  const Hmm = Math.max(10, Number(cfg.h) || 20);
  const DPMM = 8;                 // 203 dpi = 8 nuqta/mm
  const Wd = Math.round(Wmm * DPMM);
  const PRICE_FONT = '4';
  const PRICE_CW = 12;            // "4" shrift uchun taxminiy belgi eni (nuqta)
  const NARROW = 2;              // shtrix moduli kengligi (nuqta)
  let out = '';
  for (const t of tags) {
    const price = tsplSafe(t.priceText);
    const sku = tsplSafe(t.sku);
    if (!sku) continue;
    const copies = Math.max(1, Math.min(999, Number(t.count) || 1));
    // Narxni markazlaymiz
    const priceW = price.length * PRICE_CW;
    const priceX = Math.max(4, Math.round((Wd - priceW) / 2));
    // Code128 taxminiy eni: (start+data+check+stop)*11 + ~2 modul; markazlaymiz
    const modules = 11 * (sku.length + 3) + 2;
    const bcW = modules * NARROW;
    const bcX = Math.max(4, Math.round((Wd - bcW) / 2));
    out += `SIZE ${Wmm} mm,${Hmm} mm\r\n`;
    out += 'GAP 2 mm,0 mm\r\n';
    out += 'DIRECTION 1\r\n';
    out += 'REFERENCE 0,0\r\n';
    out += 'CLS\r\n';
    out += `TEXT ${priceX},14,"${PRICE_FONT}",0,1,1,"${price}"\r\n`;
    // BARCODE x,y,"128",balandlik,human_readable,burchak,narrow,wide,"matn"
    out += `BARCODE ${bcX},66,"128",74,1,0,${NARROW},${NARROW},"${sku}"\r\n`;
    out += `PRINT ${copies},1\r\n`;
  }
  return out;
}

// Yorliqlarni TSPL bilan tanlangan printerga JIM chop etadi (macOS/Linux: `lp -o raw`).
ipcMain.handle('label:print', async (_e, payload) => {
  const o = payload || {};
  const printer = String(o.printer || '').trim();
  const tags = Array.isArray(o.tags) ? o.tags : [];
  if (!printer) return { ok: false, error: 'Printer tanlanmagan' };
  if (!tags.length) return { ok: false, error: 'Chop etiladigan yorliq yo\'q' };
  if (process.platform === 'win32') {
    return { ok: false, error: 'Windows uchun xom chop etish hali sozlanmagan' };
  }
  const tspl = buildTsplLabels(tags, { w: o.w, h: o.h });
  return await new Promise((resolve) => {
    let done = false;
    const child = execFile('/usr/bin/lp', ['-d', printer, '-o', 'raw'],
      { timeout: 20000 }, (err, _stdout, stderr) => {
        if (done) return; done = true;
        if (err) resolve({ ok: false, error: String(stderr || err.message || err) });
        else resolve({ ok: true });
      });
    try { child.stdin.write(tspl); child.stdin.end(); }
    catch (e) { if (!done) { done = true; resolve({ ok: false, error: String(e) }); } }
  });
});

// ── IPC: logo (base64) ────────────────────────────────────────────────────
ipcMain.handle('assets:logo', () => {
  try {
    const p = path.join(__dirname, 'renderer', 'logo.png');
    const b64 = fs.readFileSync(p).toString('base64');
    return `data:image/png;base64,${b64}`;
  } catch { return ''; }
});
