'use strict';
// SAYT rejimi: Electron preload bo'lmasa, window.chinor shu yerda quriladi.
// So'rovlar o'sha lib/api.js orqali to'g'ridan serverga ketadi (CORS kerak),
// sessiya esa localStorage'da. Desktop ilovada bu fayl hech narsa qilmaydi.
(function () {
  if (window.chinor) return;            // Electron: preload allaqachon bergan
  const A = window.module.exports;      // lib/api.js
  const KEY = 'chinor-admin';
  const DEF = {
    serverUrl: 'https://kassa.chinorpos.com', token: '', admin: null, perms: null,
    glavniy: false, usdRate: 12500, loggedOut: false, labelPrinter: '', labelW: 30, labelH: 20,
  };
  let S;
  try { S = Object.assign({}, DEF, JSON.parse(localStorage.getItem(KEY) || '{}')); }
  catch (_) { S = Object.assign({}, DEF); }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (_) {} };

  // main.js dagi withAuth bilan bir xil javob shakli
  const authed = (fn) => async (...args) => {
    if (!S.token) return { ok: false, error: 'Avtorizatsiya talab qilinadi', authRequired: true };
    const r = await fn(S.serverUrl, S.token, ...args);
    if (r.status === 401) return { ok: false, error: 'Sessiya tugadi', authRequired: true };
    if (!r.ok || (r.data && r.data.ok === false)) {
      return { ok: false, error: (r.data && r.data.error) || 'Server xatosi', status: r.status };
    }
    return Object.assign({ ok: true }, r.data);
  };

  // preload nomi → lib/api.js nomi (argumentlar o'sha tartibda o'tadi)
  const MAP = {
    getStats: 'stats', getRecentSales: 'recentSales', getSalesSummary: 'salesSummary',
    getOrders: 'orders', getProducts: 'products', getClients: 'clients', getAdmins: 'admins',
    getSettings: 'settings', saveSettings: 'settingsSave', getSuppliers: 'suppliers',
    getCategories: 'categories', getGuides: 'guides', getLabels: 'labels', getLabel: 'label',
    getCams: 'cams', getStaff: 'staff',
  };
  [
    'productQty', 'productSave', 'productDelete', 'prixodScan', 'prixodList', 'prixodSave',
    'supplierSave', 'supplierDelete', 'productSetSupplier', 'clientSave', 'clientUpdate',
    'clientDelete', 'clientPayment', 'clientBonus', 'clientDetail', 'clientsTop', 'debtGroup',
    'clientsInsights', 'cashMovements', 'lowStock', 'categorySave', 'categoryDelete',
    'reorderSuppliers', 'reorder', 'aiAnalyze', 'photosSummary', 'photosClean', 'photosMode',
    'productImage', 'guideSave', 'guideDelete', 'guideDraft', 'labelSave', 'labelRead',
    'staffAdd', 'staffRole', 'staffRemove', 'staffStats',
  ].forEach((n) => { MAP[n] = n; });

  const bridge = {};
  Object.keys(MAP).forEach((n) => { bridge[n] = authed(A[MAP[n]]); });

  Object.assign(bridge, {
    getState: async () => Object.assign({}, S, { version: 'sayt' }),
    setServerUrl: async (url) => {
      S.serverUrl = String(url || '').trim().replace(/\/+$/, '') || DEF.serverUrl;
      save();
      return S.serverUrl;
    },
    health: async () => {
      const r = await A.health(S.serverUrl);
      return { ok: r.ok && r.data && r.data.ok !== false };
    },
    login: async (server, login, password) => {
      if (server) await bridge.setServerUrl(server);
      const r = await A.login(S.serverUrl, login, password);
      if (!r.ok || !r.data.ok) {
        return { ok: false, error: (r.data && r.data.error) || 'Login yoki parol noto\'g\'ri' };
      }
      const d = r.data;
      const perms = Array.isArray(d.permissions) ? d.permissions : null;
      Object.assign(S, {
        token: d.session_token || '', admin: d.cashier || S.admin, usdRate: d.usd_rate || S.usdRate,
        perms, glavniy: !!d.is_glavniy, loggedOut: false,
      });
      save();
      return { ok: true, admin: d.cashier, usdRate: d.usd_rate, perms, glavniy: !!d.is_glavniy };
    },
    logout: async () => { S.token = ''; S.loggedOut = true; save(); return { ok: true }; },
    noPhoto: (page, q, stock, limit) =>
      authed(A.noPhoto)(page || 0, q || '', stock !== false, limit || 100),

    // Excel: brauzer o'zi "yuklab olish" qiladi (papka tanlash oynasi yo'q)
    exportFile: async (kind, month) => {
      if (!S.token) return { ok: false, error: 'Avtorizatsiya talab qilinadi', authRequired: true };
      const url = S.serverUrl + `/api/export?type=${encodeURIComponent(kind || 'sales')}` +
        (month ? `&month=${encodeURIComponent(month)}` : '');
      try {
        const res = await fetch(url, { headers: { 'X-Session-Token': S.token } });
        if (res.status === 401) return { ok: false, error: 'Sessiya tugadi', authRequired: true };
        if (!res.ok) {
          let err = 'Xato ' + res.status;
          try { err = (await res.json()).error || err; } catch (_) {}
          return { ok: false, error: err };
        }
        const cd = res.headers.get('content-disposition') || '';
        const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
        const name = (m && decodeURIComponent(m[1])) || `${kind || 'sales'}.xlsx`;
        const a = document.createElement('a');
        a.href = URL.createObjectURL(await res.blob());
        a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 10000);
        return { ok: true, path: name };
      } catch (e) {
        return { ok: false, error: String(e.message || e) };
      }
    },

    // Chop etish: saytda printerlar ro'yxati yo'q → har doim brauzer oynasi
    getPrinters: async () => [],
    saveLabelCfg: async (cfg) => {
      cfg = cfg || {};
      S.labelPrinter = String(cfg.printer || '');
      if (cfg.w) S.labelW = Number(cfg.w) || 30;
      if (cfg.h) S.labelH = Number(cfg.h) || 20;
      save();
      return { ok: true };
    },
    printLabels: async () => ({ ok: false, error: 'Jim chop etish faqat Mac ilovasida' }),
    printHtml: (html, opts) => new Promise((resolve) => {
      const f = document.createElement('iframe');
      f.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0';
      document.body.appendChild(f);
      f.srcdoc = String((opts && opts.html) || html || '');
      f.onload = () => setTimeout(() => {
        try { f.contentWindow.focus(); f.contentWindow.print(); resolve({ ok: true }); }
        catch (e) { resolve({ ok: false, reason: String(e) }); }
        setTimeout(() => f.remove(), 1000);
      }, 350);
    }),

    getLogo: async () => '',
    // ⌘R / Ctrl+R — sahifani qayta yuklamay, ochiq bo'limni yangilash (ilovadagidek)
    onRefresh: (cb) => window.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'r') {
        e.preventDefault(); cb();
      }
    }),
  });

  window.chinor = bridge;
})();
