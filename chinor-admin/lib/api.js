'use strict';
// Server bilan aloqa qatlami (main process, Node fetch — CORS yo'q).
// Barcha so'rovlarga ngrok/cloudflare ogohlantirishini chetlab o'tuvchi header.

const COMMON_HEADERS = { 'ngrok-skip-browser-warning': 'true' };

async function req(base, path, opts = {}) {
  const { method = 'GET', token = '', body = null, timeout = 15000 } = opts;
  const url = String(base).replace(/\/+$/, '') + path;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const headers = { ...COMMON_HEADERS };
    if (token) headers['X-Session-Token'] = token;
    if (body) headers['Content-Type'] = 'application/json';
    const res = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); }
    catch { data = { ok: false, error: 'Serverdan noto\'g\'ri javob', raw: text.slice(0, 200) }; }
    return { ok: res.ok, status: res.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: { ok: false, error: e.name === 'AbortError' ? 'Vaqt tugadi' : String(e.message || e) } };
  } finally {
    clearTimeout(timer);
  }
}

module.exports = {
  health: (base) => req(base, '/api/health', { timeout: 7000 }),
  // Admin paneli kassa terminali bilan bir xil login endpointidan foydalanadi.
  login: (base, login, password) =>
    req(base, '/api/desktop/login', { method: 'POST', body: { login, password }, timeout: 15000 }),
  stats: (base, token) =>
    req(base, '/api/stats', { token, timeout: 20000 }),
  // Sotuvlar grafiklari uchun — `since` berilsa 1000 tagacha yozuv qaytadi.
  recentSales: (base, token, since = '', limit = 1000) => {
    const qs = since
      ? `since=${encodeURIComponent(since)}&limit=${limit}`
      : `limit=200`;
    return req(base, `/api/sync/recent-sales?${qs}`, { token, timeout: 30000 });
  },
  orders: (base, token) =>
    req(base, '/api/orders', { token, timeout: 20000 }),
  // `limit` — sahifa hajmi (yangi server 500 gacha qo'llaydi; eskisi e'tiborsiz
  // qoldirib 20 talik qaytaradi — klient bunga moslashadi).
  products: (base, token, page = 0, limit = 0) =>
    req(base, `/api/products?page=${page}${limit ? `&limit=${limit}` : ''}`, { token, timeout: 25000 }),
  // Prixod — mavjud qoldiqqa delta qo'shadi (manfiy bo'lsa ayiradi).
  productQty: (base, token, id, delta) =>
    req(base, '/api/product/qty', { method: 'POST', token, body: { id, delta }, timeout: 15000 }),
  // Yangi tovar (id=0 → avtomat SKU) yoki mavjudini tahrirlash.
  productSave: (base, token, prod) =>
    req(base, '/api/product/save', { method: 'POST', token, body: prod, timeout: 20000 }),
  clients: (base, token) =>
    req(base, '/api/clients', { token, timeout: 20000 }),
  admins: (base, token) =>
    req(base, '/api/admins', { token, timeout: 20000 }),
  settings: (base, token) =>
    req(base, '/api/settings', { token, timeout: 15000 }),
  // Sozlamalarni saqlash — qisman yuborish mumkin (faqat o'zgargan kalitlar).
  settingsSave: (base, token, payload) =>
    req(base, '/api/settings', { method: 'POST', token, body: payload, timeout: 15000 }),
  // AI prixod — nakladnoy rasmini yuborib, o'qilgan+katalogga moslangan qatorlarni oladi.
  // Multipart bo'lgani uchun `req` (JSON) emas, alohida fetch. AI sekin → uzun timeout.
  prixodScan: async (base, token, bytes, filename = 'nakladnoy.jpg') => {
    const url = String(base).replace(/\/+$/, '') + '/api/prixod/scan';
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 90000);
    try {
      const headers = { ...COMMON_HEADERS };
      if (token) headers['X-Session-Token'] = token;
      const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
      const fd = new FormData();
      fd.append('photo', new Blob([u8], { type: 'image/jpeg' }), filename);
      const res = await fetch(url, { method: 'POST', headers, body: fd, signal: ctrl.signal });
      const text = await res.text();
      let data;
      try { data = JSON.parse(text); }
      catch { data = { ok: false, error: 'Serverdan noto\'g\'ri javob', raw: text.slice(0, 200) }; }
      return { ok: res.ok, status: res.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: { ok: false, error: e.name === 'AbortError' ? 'Vaqt tugadi (AI sekin javob berdi)' : String(e.message || e) } };
    } finally {
      clearTimeout(timer);
    }
  },
  // Oldingi prixod hujjatlari ro'yxati (tarix) + yig'indi.
  prixodList: (base, token, from = '', to = '') => {
    const qs = [];
    if (from) qs.push('from=' + encodeURIComponent(from));
    if (to) qs.push('to=' + encodeURIComponent(to));
    return req(base, '/api/prixod/list' + (qs.length ? '?' + qs.join('&') : ''), { token, timeout: 20000 });
  },
  // Prixod hujjatini saqlash (qoldiq+narx yangilanadi va jurnalga yoziladi).
  prixodSave: (base, token, payload) =>
    req(base, '/api/prixod/save', { method: 'POST', token, body: payload, timeout: 30000 }),
  // ── Yetkazib beruvchilar (suppliers) ───────────────────────────────────────
  suppliers: (base, token) =>
    req(base, '/api/suppliers', { token, timeout: 20000 }),
  // Yangi yetkazib beruvchi (id=0) yoki mavjudini tahrirlash.
  supplierSave: (base, token, payload) =>
    req(base, '/api/supplier/save', { method: 'POST', token, body: payload, timeout: 15000 }),
  supplierDelete: (base, token, id) =>
    req(base, '/api/supplier/delete', { method: 'POST', token, body: { id }, timeout: 15000 }),
  // Mahsulotga yetkazib beruvchi biriktirish/yechish (faqat bog'lanish).
  productSetSupplier: (base, token, product_id, supplier_id) =>
    req(base, '/api/product/supplier', { method: 'POST', token, body: { product_id, supplier_id }, timeout: 15000 }),
  // ── Mijozlar boshqaruvi ─────────────────────────────────────────────────────
  clientSave: (base, token, payload) =>
    req(base, '/api/client/save', { method: 'POST', token, body: payload, timeout: 15000 }),
  clientUpdate: (base, token, payload) =>
    req(base, '/api/client/update', { method: 'POST', token, body: payload, timeout: 15000 }),
  clientDelete: (base, token, client_id) =>
    req(base, '/api/client/delete', { method: 'POST', token, body: { client_id }, timeout: 15000 }),
  // Qarz to'lovi qabul qilish: {client_id, amount, method, note}
  clientPayment: (base, token, payload) =>
    req(base, '/api/client/payment', { method: 'POST', token, body: payload, timeout: 15000 }),
  // Bonus qo'lda o'zgartirish: {client_id, delta, reason}
  clientBonus: (base, token, payload) =>
    req(base, '/api/client/bonus', { method: 'POST', token, body: payload, timeout: 15000 }),
  // Mijoz kartochkasi: statistika + xaridlar + to'lovlar + bonus tarixi
  clientDetail: (base, token, client_id) =>
    req(base, `/api/client/detail?client_id=${Number(client_id) || 0}`, { token, timeout: 20000 }),
  clientsTop: (base, token, days = 30) =>
    req(base, `/api/clients/top?days=${Number(days) || 0}`, { token, timeout: 20000 }),
  // ── Xodimlar boshqaruvi + KPI ───────────────────────────────────────────────
  staff: (base, token) =>
    req(base, '/api/staff', { token, timeout: 20000 }),
  staffAdd: (base, token, payload) =>
    req(base, '/api/staff/add', { method: 'POST', token, body: payload, timeout: 15000 }),
  staffRole: (base, token, telegram_id, role) =>
    req(base, '/api/staff/role', { method: 'POST', token, body: { telegram_id, role }, timeout: 15000 }),
  staffRemove: (base, token, payload) =>
    req(base, '/api/staff/remove', { method: 'POST', token, body: payload, timeout: 15000 }),
  staffStats: (base, token, days = 30) =>
    req(base, `/api/staff/stats?days=${Number(days) || 0}`, { token, timeout: 25000 }),
};
