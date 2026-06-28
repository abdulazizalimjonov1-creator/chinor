'use strict';
// Server bilan aloqa qatlami. Barcha so'rovlarga ngrok ogohlantirish
// sahifasini chetlab o'tuvchi header qo'shiladi (ngrok-free uchun zarur).

const COMMON_HEADERS = { 'ngrok-skip-browser-warning': 'true' };

async function req(base, path, opts = {}) {
  const { method = 'GET', token = '', body = null, timeout = 12000 } = opts;
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
  health: (base) => req(base, '/api/health', { timeout: 6000 }),
  login: (base, login, password, deviceId = '') =>
    req(base, '/api/desktop/login', { method: 'POST', body: { login, password, device_id: deviceId }, timeout: 15000 }),
  catalog: (base, token, deviceId = '') =>
    req(base, '/api/sync/catalog' + (deviceId ? ('?device_id=' + encodeURIComponent(deviceId)) : ''),
      { token, timeout: 30000 }),
  pushSales: (base, token, sales) =>
    req(base, '/api/sync/sales', { method: 'POST', token, body: { sales }, timeout: 30000 }),
  recentSales: (base, token, since = '', limit = 1000) => {
    const qs = since
      ? `since=${encodeURIComponent(since)}&limit=${limit}`
      : `limit=60`;
    return req(base, `/api/sync/recent-sales?${qs}`, { token, timeout: 30000 });
  },
  // ── To'lov-link relay (server orqali — alohida to'lov boti) ──────────
  // Yangi to'lov boshlanishi: server "shu vaqtdan keyin" belgisini qaytaradi.
  paymentBegin: (base, token) =>
    req(base, '/api/desktop/payment/begin', { method: 'POST', token, timeout: 12000 }),
  // Linkni so'rash: server `since` dan keyin egadan kelgan oxirgi linkni qaytaradi.
  paymentPoll: (base, token, since) =>
    req(base, `/api/desktop/payment/poll?since=${encodeURIComponent(since || 0)}`, { token, timeout: 15000 }),
  // Botga (egaga) xabar yuborish — server to'lov boti orqali jo'natadi.
  paymentNotify: (base, token, text) =>
    req(base, '/api/desktop/payment/notify', { method: 'POST', token, body: { text }, timeout: 12000 }),
};
