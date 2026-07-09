'use strict';
const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { execFile, spawn } = require('child_process');
const { autoUpdater } = require('electron-updater');
const api = require('./lib/api');
const { Store } = require('./lib/store');

let win = null;
let store = null;

// Yangilanish holati — renderer'dagi tugma shu obyektni ko'rsatadi.
const updateState = { available: false, version: '', downloading: false, downloaded: false, progress: 0 };
function pushUpdate() {
  if (win && !win.isDestroyed()) win.webContents.send('update:state', updateState);
}

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

  // ⌘R / Ctrl+R — to'liq reload (login holati yo'qoladi) o'rniga ochiq
  // sahifani ma'lumot bilan yangilaymiz. ⇧+R (force reload) tegmaydi.
  win.webContents.on('before-input-event', (event, input) => {
    if (input.type === 'keyDown' && (input.meta || input.control) &&
        !input.alt && !input.shift && input.key && input.key.toLowerCase() === 'r') {
      event.preventDefault();
      win.webContents.send('app:refresh');
    }
  });
}

app.whenReady().then(() => {
  store = new Store(dataFile(), app.isPackaged);
  createWindow();
  setupUpdater();
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
  version: app.getVersion(),
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
ipcMain.handle('data:products', withAuth((base, token, arg) => {
  // Eski chaqiruv: raqam (page). Yangi: { page, limit }.
  const o = (typeof arg === 'object' && arg) ? arg : { page: arg };
  return api.products(base, token, Number(o.page) || 0, Number(o.limit) || 0);
}));
ipcMain.handle('data:productQty', withAuth((base, token, arg) => api.productQty(base, token, arg.id, arg.delta)));
ipcMain.handle('data:productSave', withAuth((base, token, arg) => api.productSave(base, token, arg)));
ipcMain.handle('data:prixodScan', withAuth((base, token, arg) => api.prixodScan(base, token, arg.bytes, arg.filename)));
ipcMain.handle('data:prixodList', withAuth((base, token, arg) => api.prixodList(base, token, (arg && arg.from) || '', (arg && arg.to) || '')));
ipcMain.handle('data:prixodSave', withAuth((base, token, arg) => api.prixodSave(base, token, arg)));
ipcMain.handle('data:clients', withAuth((base, token) => api.clients(base, token)));
ipcMain.handle('data:admins', withAuth((base, token) => api.admins(base, token)));
ipcMain.handle('data:settings', withAuth((base, token) => api.settings(base, token)));
// Yetkazib beruvchilar (suppliers)
ipcMain.handle('data:suppliers', withAuth((base, token) => api.suppliers(base, token)));
ipcMain.handle('data:supplierSave', withAuth((base, token, arg) => api.supplierSave(base, token, arg)));
ipcMain.handle('data:supplierDelete', withAuth((base, token, arg) => api.supplierDelete(base, token, arg.id)));
ipcMain.handle('data:productSetSupplier', withAuth((base, token, arg) => api.productSetSupplier(base, token, arg.product_id, arg.supplier_id)));

ipcMain.handle('net:health', async () => {
  const r = await api.health(store.get('serverUrl'));
  return { ok: r.ok && r.data && r.data.ok !== false };
});

// ── IPC: yangilanish (Windows: electron-updater; macOS: o'zimizning updater) ──
ipcMain.handle('update:get', () => updateState);
ipcMain.handle('update:check', () => {
  if (process.platform === 'darwin') { macCheckForUpdates(); return true; }
  autoUpdater.checkForUpdates().catch(() => {});
  return true;
});
ipcMain.handle('update:download', () => {
  if (process.platform === 'darwin') { macDownloadUpdate(); return true; }
  updateState.downloading = true; pushUpdate();
  autoUpdater.downloadUpdate().catch(() => { updateState.downloading = false; pushUpdate(); });
  return true;
});
ipcMain.handle('update:install', () => {
  if (process.platform === 'darwin') { macInstallUpdate(); return true; }
  try { autoUpdater.quitAndInstall(true, true); } catch (_) {}
  return true;
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

// ── Avtomatik yangilanish ───────────────────────────────────────────────────
function setupUpdater() {
  autoUpdater.autoDownload = false;            // foydalanuvchi tugma orqali boshqaradi
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.requestHeaders = { 'ngrok-skip-browser-warning': 'true' };

  autoUpdater.on('update-available', (info) => {
    Object.assign(updateState, { available: true, version: info.version, downloading: false, downloaded: false, progress: 0 });
    pushUpdate();
  });
  autoUpdater.on('update-not-available', () => {
    Object.assign(updateState, { available: false, version: '', downloading: false, downloaded: false, progress: 0 });
    pushUpdate();
  });
  autoUpdater.on('download-progress', (p) => {
    updateState.downloading = true;
    updateState.progress = Math.round(p.percent || 0);
    pushUpdate();
  });
  autoUpdater.on('update-downloaded', (info) => {
    Object.assign(updateState, { downloading: false, downloaded: true, version: info.version });
    pushUpdate();
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
// macOS'da electron-updater (Squirrel.Mac) imzolangan ilovani talab qiladi.
// Ilova imzosiz bo'lgani uchun yengil updater: serverdagi latest-mac.yml ni
// tekshiramiz, .zip ni yuklab olamiz, .app ni almashtirib qayta ochamiz.
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

// Admin yangilanish feed'i — kassa bilan to'qnashmasligi uchun /updates/admin.
function updatesBase() {
  return String((store && store.get('serverUrl')) || '').replace(/\/+$/, '') + '/updates/admin';
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
      Object.assign(updateState, { available: true, version: ver, downloading: false, downloaded: false, progress: 0 });
    } else {
      macUpdate = null;
      Object.assign(updateState, { available: false, version: '', downloading: false, downloaded: false, progress: 0 });
    }
    pushUpdate();
  } catch (_) {}
}

async function macDownloadUpdate() {
  if (!macUpdate) return;
  updateState.downloading = true; updateState.progress = 0; pushUpdate();
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
    const dir = path.join(app.getPath('temp'), 'chinor-admin-update');
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
    const zipPath = path.join(dir, 'update.zip');
    fs.writeFileSync(zipPath, buf);
    macUpdate.zipPath = zipPath;
    Object.assign(updateState, { downloading: false, downloaded: true, progress: 100 });
    pushUpdate();
  } catch (e) {
    updateState.downloading = false;
    pushUpdate();
  }
}

function macInstallUpdate() {
  if (!macUpdate || !macUpdate.zipPath) return;
  const exe = app.getPath('exe');                // .../Chinor.app/Contents/MacOS/Chinor
  const appBundle = exe.split('/Contents/')[0];  // .../Chinor.app
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
