'use strict';
/* Chinor — boshqaruv paneli. Jonli ma'lumot serverdan (window.chinor bridge). */

const api = window.chinor;
const $ = (id) => document.getElementById(id);

// ── State ──────────────────────────────────────────────────────────────────
const S = {
  serverUrl: '',
  admin: null,
  usdRate: 12500,
  module: 'hisobotlar',
  tabs: [],          // [{key, title, closable, render}]
  activeTab: '',
};

// ── Formatlash / yordamchilar ───────────────────────────────────────────────
const nf = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 });
const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const money = (n) => nf.format(Math.round((Number(n) || 0) * 1000) / 1000);
const int = (n) => nf0.format(Math.round(Number(n) || 0));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function toast(msg, ms = 2600) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}

// Tashkent (UTC+5) vaqti — server formatida
function tashkentNow() { return new Date(Date.now() + 5 * 3600 * 1000); }
function ymd(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
}
// "YYYY-MM-DD HH:MM:SS" sotuv created_at'ini Date'ga (UTC sifatida) o'qish
function parseSaleTs(s) {
  if (!s) return null;
  const m = String(s).match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]));
}

// ── SVG grafiklar (tashqi kutubxonasiz) ──────────────────────────────────────
function lineChart(points, opts = {}) {
  // points: [{label, value}]
  const W = opts.width || 920, H = opts.height || 300;
  const padL = 64, padR = 16, padT = 16, padB = 46;
  if (!points.length) return `<div class="chart-empty">Ma'lumot yo'q</div>`;
  const vals = points.map((p) => p.value);
  let max = Math.max(0, ...vals), min = Math.min(0, ...vals);
  if (max === min) max = min + 1;
  const ph = H - padT - padB, pw = W - padL - padR;
  const x = (i) => padL + (points.length === 1 ? pw / 2 : (i / (points.length - 1)) * pw);
  const y = (v) => padT + ph - ((v - min) / (max - min)) * ph;

  // Y gridlines (5)
  let grid = '', ylab = '';
  for (let i = 0; i <= 4; i++) {
    const v = min + (i / 4) * (max - min);
    const yy = y(v);
    grid += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="#eef1f7" stroke-width="1"/>`;
    ylab += `<text x="${padL - 8}" y="${yy + 4}" text-anchor="end" font-size="11" fill="#9aa3bd">${int(v)}</text>`;
  }
  const zeroY = y(0);
  grid += `<line x1="${padL}" y1="${zeroY}" x2="${W - padR}" y2="${zeroY}" stroke="#d7ddee" stroke-width="1"/>`;

  const pts = points.map((p, i) => `${x(i)},${y(p.value)}`).join(' ');
  const area = `${padL},${zeroY} ${pts} ${x(points.length - 1)},${zeroY}`;
  let dots = points.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.value)}" r="2.6" fill="#2d9142"/>`).join('');

  // X labels (max ~10)
  let xlab = '';
  const step = Math.max(1, Math.ceil(points.length / 10));
  points.forEach((p, i) => {
    if (i % step === 0 || i === points.length - 1) {
      xlab += `<text x="${x(i)}" y="${H - 22}" text-anchor="middle" font-size="10" fill="#9aa3bd" transform="rotate(35 ${x(i)} ${H - 22})">${esc(p.label)}</text>`;
    }
  });

  return `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${grid}
    <polygon points="${area}" fill="rgba(45,145,66,.12)"/>
    <polyline points="${pts}" fill="none" stroke="#2d9142" stroke-width="2"/>
    ${dots}${ylab}${xlab}
  </svg>`;
}

function barChart(bars, opts = {}) {
  // bars: [{label, value}]
  const W = opts.width || 600, H = opts.height || 260;
  const padL = 56, padR = 12, padT = 12, padB = 40;
  if (!bars.length || bars.every((b) => !b.value)) return `<div class="chart-empty">Ma'lumot yo'q</div>`;
  const max = Math.max(1, ...bars.map((b) => b.value));
  const ph = H - padT - padB, pw = W - padL - padR;
  const bw = pw / bars.length;
  const y = (v) => padT + ph - (v / max) * ph;

  let grid = '', ylab = '';
  for (let i = 0; i <= 4; i++) {
    const v = (i / 4) * max, yy = y(v);
    grid += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="#eef1f7"/>`;
    ylab += `<text x="${padL - 8}" y="${yy + 4}" text-anchor="end" font-size="11" fill="#9aa3bd">${int(v)}</text>`;
  }
  let rects = '', xlab = '';
  bars.forEach((b, i) => {
    const bx = padL + i * bw + bw * 0.18;
    const w = bw * 0.64;
    const by = y(b.value), bh = padT + ph - by;
    rects += `<rect x="${bx}" y="${by}" width="${w}" height="${Math.max(0, bh)}" rx="4" fill="#2d9142"/>`;
    xlab += `<text x="${padL + i * bw + bw / 2}" y="${H - 16}" text-anchor="middle" font-size="10" fill="#9aa3bd" transform="rotate(30 ${padL + i * bw + bw / 2} ${H - 16})">${esc(b.label)}</text>`;
  });
  return `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${grid}${rects}${ylab}${xlab}</svg>`;
}

// ── Boot ─────────────────────────────────────────────────────────────────────
async function boot() {
  const st = await api.getState();
  S.serverUrl = st.serverUrl || '';
  $('serverInput').value = S.serverUrl;
  if (st.token && !st.loggedOut) {
    // Tokenni tekshiramiz
    S.admin = st.admin; S.usdRate = st.usdRate || 12500;
    const r = await api.getStats();
    if (r.ok) { enterApp(); return; }
    if (r.authRequired) { /* login kerak */ }
  }
  showLogin();
}

function showLogin() {
  $('app').classList.add('hidden');
  $('loginScreen').classList.remove('hidden');
  $('loginInput').focus();
}

async function doLogin() {
  const login = $('loginInput').value.trim();
  const pass = $('passInput').value.trim();
  const server = $('serverInput').value.trim();
  const errEl = $('loginErr');
  errEl.textContent = '';
  if (!login || !pass) { errEl.textContent = 'Login va parolni kiriting'; return; }
  const btn = $('loginBtn');
  btn.disabled = true; btn.textContent = 'Kirilyapti...';
  const r = await api.login(server, login, pass);
  btn.disabled = false; btn.textContent = 'Kirish';
  if (!r.ok) { errEl.textContent = r.error || 'Kirish amalga oshmadi'; return; }
  S.admin = r.admin; S.usdRate = r.usdRate || 12500;
  S.serverUrl = server || S.serverUrl;
  enterApp();
}

function enterApp() {
  $('loginScreen').classList.add('hidden');
  $('app').classList.remove('hidden');
  const name = (S.admin && S.admin.name) || 'Admin';
  $('adminName').textContent = name;
  $('adminAva').textContent = (name[0] || 'A').toUpperCase();
  renderNav();
  selectModule('hisobotlar');
}

async function doLogout() {
  await api.logout();
  S.admin = null; S.tabs = []; S.activeTab = '';
  showLogin();
}

// ── Sidebar ───────────────────────────────────────────────────────────────
const MODULES = [
  { key: 'hisobotlar', label: 'Hisobotlar', icon: 'M3 13h4v8H3zM10 3h4v18h-4zM17 9h4v12h-4z' },
  { key: 'tovarlar', label: 'Tovarlar', icon: 'M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4zM3 6h18M16 10a4 4 0 0 1-8 0' },
  { key: 'ombor', label: 'Ombor', icon: 'M21 8 12 3 3 8v13h18zM3 8l9 5 9-5M9 21v-6h6v6' },
  { key: 'moliya', label: 'Moliya', icon: 'M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6' },
  { key: 'xodimlar', label: 'Xodimlar', icon: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75' },
  { key: 'mijozlar', label: 'Mijozlar', icon: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z' },
  { key: 'sozlamalar', label: 'Sozlamalar', icon: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z' },
];

function renderNav() {
  const nav = $('nav');
  nav.innerHTML = MODULES.map((m) => `
    <button class="nav-item${m.key === S.module ? ' active' : ''}" data-mod="${m.key}">
      <svg class="ico" viewBox="0 0 24 24"><path d="${m.icon}"/></svg>${m.label}
    </button>`).join('');
  nav.querySelectorAll('.nav-item').forEach((b) =>
    b.addEventListener('click', () => selectModule(b.dataset.mod)));
}

// ── Tab tizimi ──────────────────────────────────────────────────────────────
function selectModule(mod) {
  S.module = mod;
  renderNav();
  // Har modul o'z "menyu" tab'i bilan boshlanadi
  S.tabs = [{ key: mod + ':menu', title: moduleTitle(mod), closable: false, render: () => renderModuleMenu(mod) }];
  S.activeTab = S.tabs[0].key;
  renderTabs();
  renderActive();
}

function moduleTitle(mod) {
  return ({ hisobotlar: 'Hisobotlar menyusi', tovarlar: 'Tovarlar menyusi', ombor: 'Ombor', moliya: 'Moliya', xodimlar: 'Xodimlar', mijozlar: 'Mijozlar', sozlamalar: 'Sozlamalar' })[mod] || mod;
}

function openTab(key, title, render) {
  const existing = S.tabs.find((t) => t.key === key);
  if (!existing) S.tabs.push({ key, title, closable: true, render });
  S.activeTab = key;
  renderTabs();
  renderActive();
}

function closeTab(key) {
  const i = S.tabs.findIndex((t) => t.key === key);
  if (i < 0) return;
  S.tabs.splice(i, 1);
  if (S.activeTab === key) S.activeTab = S.tabs[Math.max(0, i - 1)].key;
  renderTabs();
  renderActive();
}

function renderTabs() {
  const strip = $('tabStrip');
  strip.innerHTML = S.tabs.map((t) => `
    <button class="tab${t.key === S.activeTab ? ' active' : ''}" data-key="${t.key}">
      <span>${esc(t.title)}</span>
      ${t.closable ? `<span class="tab-x" data-close="${t.key}">✕</span>` : ''}
    </button>`).join('');
  strip.querySelectorAll('.tab').forEach((b) => b.addEventListener('click', (e) => {
    const close = e.target.closest('[data-close]');
    if (close) { closeTab(close.dataset.close); return; }
    S.activeTab = b.dataset.key; renderTabs(); renderActive();
  }));
}

function renderActive() {
  const tab = S.tabs.find((t) => t.key === S.activeTab);
  if (tab) tab.render($('content'));
}

// ── Modul menyulari (tile'lar) ───────────────────────────────────────────────
function tilesHtml(items) {
  return `<div class="tiles">${items.map((it) => `
    <button class="tile${it.soon ? ' soon' : ''}" data-tile="${it.key}">
      <svg class="ico" viewBox="0 0 24 24"><path d="${it.icon}"/></svg>
      <span>${esc(it.label)}${it.soon ? ' <small>(tez orada)</small>' : ''}</span>
    </button>`).join('')}</div>`;
}

function renderModuleMenu(mod) {
  const c = $('content');
  if (mod === 'hisobotlar') {
    c.innerHTML = tilesHtml([
      { key: 'sotuvlar', label: 'Sotuvlar hisoboti', icon: 'M3 3v18h18M7 14l4-4 3 3 5-6' },
      { key: 'cheklar', label: 'Cheklar', icon: 'M6 2h9l3 3v17l-2-1-2 1-2-1-2 1-2-1-2 1V4a2 2 0 0 1 2-2zM9 8h6M9 12h6M9 16h4' },
      { key: 'online', label: 'Online savdo', icon: 'M2 3h20v14H2zM8 21h8M12 17v4M6 8l3 3 3-4 3 3' },
    ]);
    bindTiles(c, {
      sotuvlar: () => openTab('rep:sotuvlar', 'Sotuvlar hisoboti', renderSalesReport),
      cheklar: () => openTab('rep:cheklar', 'Cheklar', renderReceipts),
      online: () => openTab('rep:online', 'Online savdo', renderOnlineReport),
    });
  } else if (mod === 'tovarlar') {
    c.innerHTML = tilesHtml([
      { key: 'list', label: 'Tovarlar', icon: 'M3 6h18M3 12h18M3 18h18' },
      { key: 'cat', label: 'Kategoriyalar', icon: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z', soon: true },
      { key: 'fast', label: 'Tez sotiladigan', icon: 'M13 2 3 14h7l-1 8 10-12h-7z', soon: true },
      { key: 'kg', label: 'Kiloli tovarlar', icon: 'M12 3a3 3 0 0 0-3 3h6a3 3 0 0 0-3-3zM5 6h14l2 15H3z', soon: true },
      { key: 'labels', label: 'Sennik chop etish', icon: 'M20 12V8a2 2 0 0 0-2-2H6L2 12l4 6h12a2 2 0 0 0 2-2zM6 9h.01' },
      { key: 'serial', label: 'Seriyali tovarlar', icon: 'M3 6h18v12H3zM7 9v6M11 9v6M15 9v6M19 9v6', soon: true },
      { key: 'pricehist', label: 'Narxlar tarixi', icon: 'M12 8v4l3 3M3.05 11a9 9 0 1 1 .5 4', soon: true },
    ]);
    bindTiles(c, {
      list: () => openTab('tov:list', 'Tovarlar', renderProducts),
      labels: () => openTab('tov:labels', 'Sennik chop etish', renderPriceTags),
    });
  } else if (mod === 'ombor') {
    c.innerHTML = tilesHtml([
      { key: 'prixod', label: 'Tovar prixod (kirim)', icon: 'M21 8 12 3 3 8v13h18zM3 8l9 5 9-5M12 22v-9M12 13l-3-2M12 13l3-2' },
      { key: 'invent', label: 'Inventarizatsiya', icon: 'M9 11l3 3 8-8M3 6h12M3 12h6M3 18h6', soon: true },
      { key: 'writeoff', label: 'Hisobdan chiqarish', icon: 'M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2', soon: true },
    ]);
    bindTiles(c, { prixod: () => openTab('omb:prixod', 'Tovar prixod', renderPrixod) });
  } else if (mod === 'mijozlar') {
    renderClients(c);
  } else if (mod === 'xodimlar') {
    renderAdmins(c);
  } else if (mod === 'sozlamalar') {
    renderSettings(c);
  } else {
    c.innerHTML = `<div class="card"><div class="card-title">${esc(moduleTitle(mod))}</div>
      <div class="chart-empty">Bu bo'lim keyingi bosqichda qo'shiladi.</div></div>`;
  }
}

function bindTiles(c, map) {
  c.querySelectorAll('.tile').forEach((b) => {
    const fn = map[b.dataset.tile];
    if (fn && !b.classList.contains('soon')) b.addEventListener('click', fn);
  });
}

// ── HISOBOT: Sotuvlar ────────────────────────────────────────────────────────
async function renderSalesReport(c) {
  c.innerHTML = `<div class="loading">Yuklanyapti…</div>`;
  const range = renderSalesReport._range || 'today';

  const statsR = await api.getStats();
  if (!statsR.ok) {
    if (statsR.authRequired) return doLogout();
    c.innerHTML = `<div class="err-box">Server xatosi: ${esc(statsR.error || '')}</div>`;
    return;
  }
  const blk = range === 'month' ? statsR.month : statsR.today;
  const tashNow = tashkentNow();
  const since = range === 'month'
    ? `${ymd(tashNow).slice(0, 7)}-01 00:00:00`
    : `${ymd(tashNow)} 00:00:00`;

  const salesR = await api.getRecentSales(since);
  const sales = (salesR.ok && salesR.sales) ? salesR.sales : [];

  const revenue = blk.revenue_sum || 0;
  const profit = blk.profit_sum || 0;
  const checks = blk.sale_count || 0;
  const avg = checks ? revenue / checks : 0;

  // Grafiklar uchun real sotuvlarni filtrlash (eng eski → eng yangi)
  const ordered = sales.slice().reverse();
  const linePts = ordered.map((s) => {
    const t = parseSaleTs(s.created_at);
    const hh = t ? `${String((t.getUTCHours()) % 24).padStart(2, '0')}:${String(t.getUTCMinutes()).padStart(2, '0')}` : '';
    const sign = s.is_return ? -1 : 1;
    return { label: hh, value: sign * (s.total_sum || 0) };
  });

  // To'lov turlari
  const pay = { cash: 0, card: 0, click: 0, qarz: 0 };
  sales.forEach((s) => {
    const v = s.total_sum || 0;
    if (s.is_return) return;
    if (s.payment === 'cash') pay.cash += v;
    else if (s.payment === 'card') pay.card += v;
    else if (s.payment === 'click') pay.click += v;
    else if (s.payment === 'qarz' || s.is_nasiya) pay.qarz += v;
  });
  const payRows = [
    { name: 'Naqd', v: pay.cash }, { name: 'Karta', v: pay.card },
    { name: 'Click', v: pay.click }, { name: 'Qarz', v: pay.qarz },
  ];
  const payMax = Math.max(1, ...payRows.map((r) => r.v));
  const payTotal = payRows.reduce((a, r) => a + r.v, 0);

  // Soat bo'yicha
  const byHour = new Array(24).fill(0);
  sales.forEach((s) => {
    if (s.is_return) return;
    const t = parseSaleTs(s.created_at);
    if (t) byHour[t.getUTCHours()] += (s.total_sum || 0);
  });
  const hourBars = byHour.map((v, h) => ({ label: `${String(h).padStart(2, '0')}:00`, value: v }))
    .filter((b, h) => h >= 6 && h <= 23);

  // Hafta kuni bo'yicha
  const wd = ['Yakshanba', 'Dushanba', 'Seshanba', 'Chorshanba', 'Payshanba', 'Juma', 'Shanba'];
  const byWd = new Array(7).fill(0);
  sales.forEach((s) => {
    if (s.is_return) return;
    const t = parseSaleTs(s.created_at);
    if (t) byWd[t.getUTCDay()] += (s.total_sum || 0);
  });
  const wdOrder = [1, 2, 3, 4, 5, 6, 0];
  const wdBars = wdOrder.map((i) => ({ label: wd[i], value: byWd[i] }));

  c.innerHTML = `
    <div class="filters">
      <button class="btn-soft${range === 'today' ? '' : ' ghost'}" data-range="today">Bugun</button>
      <button class="btn-soft${range === 'month' ? '' : ' ghost'}" data-range="month">Bu oy</button>
      <div class="filter"><label>Davr</label><span>${range === 'month' ? esc(statsR.month_label || '') : esc(ymd(tashNow))}</span></div>
    </div>

    <div class="kpi-wrap"><div class="kpi-grid">
      <div class="kpi lead"><div class="kpi-head">${range === 'month' ? 'Bu oy' : 'Bugun'}</div><div class="kpi-sub">${range === 'month' ? esc(statsR.month_label || '') : esc(ymd(tashNow))}</div></div>
      <div class="kpi"><div class="kpi-val">${money(revenue)}</div><div class="kpi-lbl">Tushum</div></div>
      <div class="kpi"><div class="kpi-val green">${money(profit)}</div><div class="kpi-lbl">Foyda</div></div>
      <div class="kpi"><div class="kpi-val">${int(checks)}</div><div class="kpi-lbl">Cheklar</div></div>
      <div class="kpi"><div class="kpi-val">${money(avg)}</div><div class="kpi-lbl">O'rtacha chek</div></div>
    </div></div>

    <div class="card">
      <div class="card-title">Sotuvlar</div>
      ${lineChart(linePts)}
    </div>

    <div class="card">
      <div class="card-title">To'lov turlari bo'yicha sotuvlar</div>
      <div class="paybars">
        ${payRows.map((r) => `
          <div class="paybar-row">
            <div class="paybar-name">${r.name}</div>
            <div class="paybar-track"><div class="paybar-fill" style="width:${(r.v / payMax) * 100}%"></div></div>
            <div class="paybar-val">${money(r.v)}</div>
          </div>`).join('')}
      </div>
      <div class="paybar-total">Jami: ${money(payTotal)}</div>
    </div>

    <div class="card-row">
      <div class="card"><div class="card-title">Soatlar bo'yicha to'lovlar</div>${barChart(hourBars, { width: 560 })}</div>
      <div class="card"><div class="card-title">Hafta kunlari bo'yicha to'lovlar</div>${barChart(wdBars, { width: 560 })}</div>
    </div>`;

  c.querySelectorAll('[data-range]').forEach((b) => b.addEventListener('click', () => {
    renderSalesReport._range = b.dataset.range;
    renderSalesReport(c);
  }));
}

// ── HISOBOT: Online savdo (Telegram Mini App buyurtmalari) ───────────────────
async function renderOnlineReport(c) {
  c.innerHTML = `<div class="loading">Yuklanyapti…</div>`;
  const r = await api.getOrders();
  if (!r.ok) {
    if (r.authRequired) return doLogout();
    c.innerHTML = `<div class="err-box">Server xatosi: ${esc(r.error || '')}</div>`;
    return;
  }
  const items = r.items || [];
  const total = items.reduce((a, o) => a + (o.total || 0), 0);
  const byStatus = {};
  items.forEach((o) => { const k = o.status || 'yangi'; byStatus[k] = (byStatus[k] || 0) + 1; });

  const statusPill = (s) => {
    const k = (s || '').toLowerCase();
    if (k.includes('bajar') || k.includes('yakun') || k.includes('done') || k.includes('complete')) return 'green';
    if (k.includes('bekor') || k.includes('cancel') || k.includes('rad')) return 'red';
    if (k.includes('jarayon') || k.includes('process') || k.includes('qabul')) return 'blue';
    return 'gold';
  };

  c.innerHTML = `
    <div class="sum-chips">
      <div class="sum-chip"><div class="v">${int(items.length)}</div><div class="l">Buyurtmalar (so'nggi)</div></div>
      <div class="sum-chip"><div class="v">${money(total)}</div><div class="l">Umumiy summa</div></div>
      ${Object.entries(byStatus).map(([k, v]) => `<div class="sum-chip"><div class="v">${int(v)}</div><div class="l">${esc(k)}</div></div>`).join('')}
    </div>
    <div class="card" style="padding:0">
      <div class="tbl-wrap">
        <table class="tbl">
          <thead><tr><th>№</th><th>Mijoz</th><th>Telefon</th><th class="num">Summa</th><th>Holat</th><th>Vaqt</th></tr></thead>
          <tbody>
            ${items.length ? items.map((o) => `
              <tr>
                <td>#${esc(o.id)}</td>
                <td>${esc(o.shop_name || '—')}</td>
                <td>${esc(o.phone || '—')}</td>
                <td class="num">${money(o.total)}</td>
                <td><span class="pill ${statusPill(o.status)}">${esc(o.status || 'yangi')}</span></td>
                <td>${esc(o.created_at || '')}</td>
              </tr>`).join('')
              : `<tr><td colspan="6"><div class="chart-empty">Online buyurtmalar yo'q</div></td></tr>`}
          </tbody>
        </table>
      </div>
    </div>`;
}

// ── Tovarlar ro'yxati ─────────────────────────────────────────────────────────
async function renderProducts(c) {
  c.innerHTML = `<div class="loading">Yuklanyapti…</div>`;
  let all = [], page = 0, guard = 0;
  while (guard++ < 200) {
    const r = await api.getProducts(page);
    if (!r.ok) {
      if (r.authRequired) return doLogout();
      c.innerHTML = `<div class="err-box">Server xatosi: ${esc(r.error || '')}</div>`;
      return;
    }
    all = all.concat(r.items || []);
    if (!r.has_more) break;
    page++;
  }
  c.innerHTML = `
    <div class="sum-chips">
      <div class="sum-chip"><div class="v">${int(all.length)}</div><div class="l">Tovarlar (aktiv)</div></div>
      <div class="sum-chip"><div class="v">${int(all.filter((p) => (p.qty || 0) <= 0).length)}</div><div class="l">Qoldiq tugagan</div></div>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th>SKU</th><th>Nomi</th><th class="num">Qoldiq</th><th>Birlik</th><th class="num">Tannarx</th><th class="num">Sotuv narxi</th><th class="num">Ulgurji</th></tr></thead>
        <tbody>
          ${all.map((p) => `
            <tr>
              <td>${esc(p.id)}</td>
              <td>${esc(p.name)}</td>
              <td class="num">${money(p.qty)}</td>
              <td>${esc(p.unit || 'dona')}</td>
              <td class="num">${money(p.cost_price_sum || 0)}</td>
              <td class="num">${money(p.sell_price_sum || p.price_sum || 0)}</td>
              <td class="num">${money(p.wholesale_sum || 0)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

// ── Mijozlar ───────────────────────────────────────────────────────────────
async function renderClients(c) {
  c.innerHTML = `<div class="loading">Yuklanyapti…</div>`;
  const r = await api.getClients();
  if (!r.ok) {
    if (r.authRequired) return doLogout();
    c.innerHTML = `<div class="err-box">Server xatosi: ${esc(r.error || '')}</div>`;
    return;
  }
  const items = r.items || [];
  const debt = items.reduce((a, x) => a + (x.debt_sum || 0), 0);
  c.innerHTML = `
    <div class="sum-chips">
      <div class="sum-chip"><div class="v">${int(items.length)}</div><div class="l">Mijozlar</div></div>
      <div class="sum-chip"><div class="v">${money(debt)}</div><div class="l">Umumiy qarz</div></div>
    </div>
    <div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>№</th><th>Nomi</th><th>Telefon</th><th>Turi</th><th class="num">Qarz</th></tr></thead>
      <tbody>${items.length ? items.map((x) => `
        <tr><td>#${esc(x.id)}</td><td>${esc(x.shop_name || '—')}</td><td>${esc(x.phone || '—')}</td>
        <td><span class="pill ${x.is_internal ? 'blue' : 'gray'}">${x.is_internal ? 'Ichki' : esc(x.client_type || 'dona')}</span></td>
        <td class="num">${money(x.debt_sum)}</td></tr>`).join('')
        : `<tr><td colspan="5"><div class="chart-empty">Mijozlar yo'q</div></td></tr>`}</tbody>
    </table></div>`;
}

// ── Xodimlar (adminlar) ──────────────────────────────────────────────────────
async function renderAdmins(c) {
  c.innerHTML = `<div class="loading">Yuklanyapti…</div>`;
  const r = await api.getAdmins();
  if (!r.ok) {
    if (r.authRequired) return doLogout();
    c.innerHTML = `<div class="err-box">Server xatosi: ${esc(r.error || '')}</div>`;
    return;
  }
  const items = r.items || [];
  c.innerHTML = `
    <div class="sum-chips"><div class="sum-chip"><div class="v">${int(items.length)}</div><div class="l">Xodimlar</div></div></div>
    <div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Ism</th><th>Username</th><th>Rol</th><th>Qo'shilgan</th></tr></thead>
      <tbody>${items.length ? items.map((a) => `
        <tr><td>${esc(a.full_name || '—')}</td><td>${a.username ? '@' + esc(a.username) : '—'}</td>
        <td><span class="pill blue">${esc(a.role || 'full')}</span></td><td>${esc(a.created_at || '')}</td></tr>`).join('')
        : `<tr><td colspan="4"><div class="chart-empty">Xodimlar yo'q</div></td></tr>`}</tbody>
    </table></div>`;
}

// ── Sozlamalar ────────────────────────────────────────────────────────────
async function renderSettings(c) {
  c.innerHTML = `<div class="loading">Yuklanyapti…</div>`;
  const r = await api.getSettings();
  if (!r.ok) {
    if (r.authRequired) return doLogout();
    c.innerHTML = `<div class="err-box">Server xatosi: ${esc(r.error || '')}</div>`;
    return;
  }
  const s = r.settings || {};
  const rows = [
    ['USD kurs', money(s.usd_rate)],
    ['Ulgurji narx', s.wholesale_enabled ? 'Yoqilgan' : 'O\'chiq'],
    ['Shtrix-kod', s.barcode_enabled ? 'Yoqilgan' : 'O\'chiq'],
    ['Mijoz buyurtmalari', s.client_orders_enabled ? 'Yoqilgan' : 'O\'chiq'],
    ['Nasiya (qarz)', s.nasiya_enabled ? 'Yoqilgan' : 'O\'chiq'],
    ['Mini App', s.mini_app_enabled ? 'Yoqilgan' : 'O\'chiq'],
    ['Kategoriyalar', s.categories_enabled ? 'Yoqilgan' : 'O\'chiq'],
  ];
  c.innerHTML = `
    <div class="card" style="max-width:560px">
      <div class="card-title">Sozlamalar</div>
      <div class="filter" style="margin-bottom:14px"><label>Server manzili</label><span>${esc(S.serverUrl)}</span></div>
      <table class="tbl">
        <tbody>${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${esc(v)}</td></tr>`).join('')}</tbody>
      </table>
    </div>`;
}

// ── Umumiy: barcha mahsulotlarni yuklash (sahifalab) ─────────────────────────
async function fetchAllProducts() {
  let all = [], page = 0, guard = 0;
  while (guard++ < 200) {
    const r = await api.getProducts(page);
    if (!r.ok) return { ok: false, error: r.error, authRequired: r.authRequired };
    all = all.concat(r.items || []);
    if (!r.has_more) break;
    page++;
  }
  return { ok: true, items: all };
}

function paymentMeta(pay) {
  return ({
    cash: { label: '💵 Naqd', cls: 'green' },
    card: { label: '💳 Karta', cls: 'blue' },
    click: { label: '⚡ Click', cls: 'blue' },
    qarz: { label: '🤝 Qarz', cls: 'gold' },
    rasxod: { label: '📦 Rasxod', cls: 'gray' },
    qaytarish: { label: '↩️ Qaytarish', cls: 'red' },
  })[pay] || { label: esc(pay || ''), cls: 'gray' };
}

// ── HISOBOT: Cheklar (barcha cheklar ro'yxati + detal) ───────────────────────
async function renderReceipts(c) {
  const st = renderReceipts;
  if (st._pay == null) st._pay = '';
  c.innerHTML = `<div class="loading">Yuklanyapti…</div>`;
  const since = st._date ? `${st._date} 00:00:00` : '';
  const r = await api.getRecentSales(since);
  if (!r.ok) {
    if (r.authRequired) return doLogout();
    c.innerHTML = `<div class="err-box">Server xatosi: ${esc(r.error || '')}</div>`;
    return;
  }
  let sales = r.sales || [];
  if (st._date) sales = sales.filter((s) => (s.created_at || '').slice(0, 10) === st._date);
  st._all = sales;
  if (st._selId && !sales.some((s) => s.id === st._selId)) st._selId = null;
  drawReceipts(c);
}

function drawReceipts(c) {
  const st = renderReceipts;
  const q = (st._q || '').toLowerCase();
  let list = st._all || [];
  if (st._pay) list = list.filter((s) => s.payment === st._pay);
  if (q) list = list.filter((s) =>
    (s.receipt_no || '').toLowerCase().includes(q) ||
    (s.client_name || '').toLowerCase().includes(q) ||
    (s.items || []).some((it) => (it.name || '').toLowerCase().includes(q)));

  const total = list.reduce((a, s) => a + (s.is_return ? -1 : 1) * (s.total_sum || 0), 0);
  const sel = list.find((s) => s.id === st._selId) || null;
  const chips = [['', 'Hammasi'], ['cash', 'Naqd'], ['card', 'Karta'], ['click', 'Click'], ['qarz', 'Qarz'], ['qaytarish', 'Qaytarish']];

  c.innerHTML = `
    <div class="filters">
      <div class="filter"><label>Sana</label><input id="rcDate" type="date" value="${st._date || ''}"></div>
      <div class="filter" style="flex:1; min-width:220px"><label>Qidiruv</label>
        <input id="rcSearch" type="text" placeholder="Chek № · mijoz · tovar nomi" value="${esc(st._q || '')}"></div>
      <button class="btn-soft ghost" id="rcClear">Tozalash</button>
    </div>
    <div class="rc-chips">${chips.map(([k, l]) =>
      `<button class="rc-chip${st._pay === k ? ' active' : ''}" data-pay="${k}">${l}</button>`).join('')}</div>
    <div class="sum-chips" style="margin:14px 0">
      <div class="sum-chip"><div class="v">${int(list.length)}</div><div class="l">Cheklar</div></div>
      <div class="sum-chip"><div class="v">${money(total)}</div><div class="l">Jami summa</div></div>
    </div>
    <div class="master-detail">
      <div class="md-list">
        ${list.length ? list.map((s) => {
          const pm = paymentMeta(s.payment);
          return `<div class="md-row${s.id === st._selId ? ' active' : ''}" data-id="${s.id}">
            <div class="md-row-top"><b>#${esc(s.receipt_no || s.id)}</b><span class="pill ${pm.cls}">${pm.label}</span></div>
            <div class="md-row-sub"><span>${esc((s.created_at || '').slice(0, 16))}</span><b class="num">${money((s.is_return ? -1 : 1) * (s.total_sum || 0))}</b></div>
            ${s.client_name ? `<div class="md-row-cli">👤 ${esc(s.client_name)}</div>` : ''}
          </div>`;
        }).join('') : `<div class="chart-empty">Chek topilmadi</div>`}
      </div>
      <div class="md-detail">${sel ? receiptDetailHtml(sel) : `<div class="chart-empty">Chap tomondan chekni tanlang</div>`}</div>
    </div>`;

  const re = (id) => document.getElementById(id);
  re('rcDate').addEventListener('change', (e) => { st._date = e.target.value; st._selId = null; renderReceipts(c); });
  re('rcSearch').addEventListener('input', (e) => { st._q = e.target.value; drawReceipts(c); });
  re('rcClear').addEventListener('click', () => { st._date = ''; st._q = ''; st._pay = ''; st._selId = null; renderReceipts(c); });
  c.querySelectorAll('.rc-chip').forEach((b) => b.addEventListener('click', () => { st._pay = b.dataset.pay; drawReceipts(c); }));
  c.querySelectorAll('.md-row').forEach((row) => row.addEventListener('click', () => { st._selId = Number(row.dataset.id); drawReceipts(c); }));
}

function receiptDetailHtml(s) {
  const pm = paymentMeta(s.payment);
  const items = s.items || [];
  return `
    <div class="rc-d-head">
      <div><div class="rc-d-no">Chek #${esc(s.receipt_no || s.id)}</div>
        <div class="rc-d-meta">${esc((s.created_at || '').slice(0, 16))} · ${esc(s.cashier_name || '')}</div></div>
      <span class="pill ${pm.cls}">${pm.label}</span>
    </div>
    ${s.client_name ? `<div class="rc-d-cli">👤 ${esc(s.client_name)}</div>` : ''}
    <table class="tbl" style="margin-top:10px">
      <thead><tr><th>Nomi</th><th class="num">Miqdor</th><th class="num">Narx</th><th class="num">Summa</th></tr></thead>
      <tbody>${items.map((it) => `<tr>
        <td>${esc(it.name)}</td>
        <td class="num">${money(it.qty)}</td>
        <td class="num">${money(it.price_sum)}</td>
        <td class="num">${money((it.qty || 0) * (it.price_sum || 0))}</td>
      </tr>`).join('') || `<tr><td colspan="4"><div class="chart-empty">Tovarlar yo'q</div></td></tr>`}</tbody>
    </table>
    <div class="rc-d-tot">
      ${s.discount_sum ? `<div><span>Chegirma</span><b>${money(s.discount_sum)}</b></div>` : ''}
      <div class="rc-d-grand"><span>Jami</span><b>${money(s.total_sum)} so'm</b></div>
    </div>`;
}

// ── TOVARLAR: Sennik (narx teglari) chop etish ───────────────────────────────
async function renderPriceTags(c) {
  const st = renderPriceTags;
  if (!st._sel) st._sel = new Map();   // id → count
  c.innerHTML = `<div class="loading">Yuklanyapti…</div>`;
  const r = await fetchAllProducts();
  if (!r.ok) {
    if (r.authRequired) return doLogout();
    c.innerHTML = `<div class="err-box">Server xatosi: ${esc(r.error || '')}</div>`;
    return;
  }
  st._items = r.items;
  // Printerlar ro'yxati + saqlangan sennik sozlamasi (bir marta, tab ochilganda)
  if (!st._cfg) {
    try { st._printers = await api.getPrinters(); } catch (_) { st._printers = []; }
    if (!Array.isArray(st._printers)) st._printers = [];
    const S = await api.getState();
    st._cfg = { printer: S.labelPrinter || '', w: S.labelW || 30, h: S.labelH || 20 };
  }
  drawPriceTags(c);
}

function drawPriceTags(c) {
  const st = renderPriceTags;
  const q = (st._q || '').toLowerCase();
  let items = st._items || [];
  if (q) items = items.filter((p) =>
    (p.name || '').toLowerCase().includes(q) ||
    String(p.id).includes(q) ||
    (p.barcode || '').includes(q));
  items = items.slice(0, 300);
  const totalTags = [...st._sel.values()].reduce((a, n) => a + n, 0);

  const cfg = st._cfg || { printer: '', w: 30, h: 20 };
  const printers = st._printers || [];
  const prOpts = ['<option value="">Tizim oynasi (tanlash)</option>']
    .concat(printers.map((p) =>
      `<option value="${esc(p.name)}" ${p.name === cfg.printer ? 'selected' : ''}>${esc(p.display || p.name)}</option>`))
    .join('');

  c.innerHTML = `
    <div class="filters">
      <div class="filter" style="flex:1; min-width:200px"><label>Tovar qidirish</label>
        <input id="ptSearch" type="text" placeholder="Nomi · SKU · barcode" value="${esc(st._q || '')}"></div>
      <div class="filter" style="min-width:180px"><label>Printer</label>
        <select id="ptPrinter">${prOpts}</select></div>
      <div class="filter" style="width:84px"><label>En (mm)</label>
        <input id="ptW" type="number" min="10" step="1" value="${esc(cfg.w)}"></div>
      <div class="filter" style="width:84px"><label>Bo'yi (mm)</label>
        <input id="ptH" type="number" min="10" step="1" value="${esc(cfg.h)}"></div>
      <button class="btn-soft" id="ptPrint">🖨 Chop etish (${int(totalTags)} ta)</button>
      <button class="btn-soft ghost" id="ptClear">Tozalash</button>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th style="width:42px"></th><th>SKU</th><th>Nomi</th><th class="num">Narx</th><th class="num" style="width:120px">Nusxa</th></tr></thead>
        <tbody>
          ${items.map((p) => {
            const cnt = st._sel.get(p.id) || 0;
            const price = p.sell_price_sum || p.price_sum || 0;
            return `<tr data-id="${p.id}">
              <td><input type="checkbox" class="pt-chk" data-id="${p.id}" ${cnt ? 'checked' : ''}></td>
              <td>${esc(p.id)}</td>
              <td>${esc(p.name)}</td>
              <td class="num">${money(price)}</td>
              <td class="num"><input type="number" min="0" step="1" class="pt-cnt qty-inp" data-id="${p.id}" value="${cnt || 1}"></td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;

  const re = (id) => document.getElementById(id);
  re('ptSearch').addEventListener('input', (e) => { st._q = e.target.value; drawPriceTags(c); });
  re('ptClear').addEventListener('click', () => { st._sel.clear(); drawPriceTags(c); });
  re('ptPrint').addEventListener('click', () => doPrintTags(c));
  // Printer / yorliq o'lchami — o'zgarsa saqlaymiz (qayta render shart emas)
  const saveCfg = () => { try { api.saveLabelCfg(st._cfg); } catch (_) {} };
  re('ptPrinter').addEventListener('change', (e) => { st._cfg.printer = e.target.value; saveCfg(); });
  re('ptW').addEventListener('change', (e) => { st._cfg.w = Math.max(10, parseInt(e.target.value, 10) || 30); saveCfg(); });
  re('ptH').addEventListener('change', (e) => { st._cfg.h = Math.max(10, parseInt(e.target.value, 10) || 20); saveCfg(); });
  c.querySelectorAll('.pt-chk').forEach((chk) => chk.addEventListener('change', (e) => {
    const id = Number(chk.dataset.id);
    if (e.target.checked) {
      const inp = c.querySelector(`.pt-cnt[data-id="${id}"]`);
      st._sel.set(id, Math.max(1, parseInt(inp && inp.value, 10) || 1));
    } else st._sel.delete(id);
    // faqat hisoblagichni yangilaymiz (fokus yo'qolmasin)
    re('ptPrint').textContent = `🖨 Chop etish (${int([...st._sel.values()].reduce((a, n) => a + n, 0))} ta)`;
  }));
  c.querySelectorAll('.pt-cnt').forEach((inp) => inp.addEventListener('input', () => {
    const id = Number(inp.dataset.id);
    const n = Math.max(0, parseInt(inp.value, 10) || 0);
    if (n > 0) { st._sel.set(id, n); const chk = c.querySelector(`.pt-chk[data-id="${id}"]`); if (chk) chk.checked = true; }
    else st._sel.delete(id);
    re('ptPrint').textContent = `🖨 Chop etish (${int([...st._sel.values()].reduce((a, n2) => a + n2, 0))} ta)`;
  }));
}

// Narxni faqat raqam qilib formatlaydi (probel = mingliklar), "so'm" YO'Q.
// TSPL ichki shrifti ASCII bo'lgani uchun oddiy probel ishlatamiz (nbsp emas).
function priceFmt(n) {
  return String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

async function doPrintTags(c) {
  const st = renderPriceTags;
  const cfg = st._cfg || { printer: '', w: 30, h: 20 };
  if (!cfg.printer) { toast('Avval printer tanlang'); return; }

  // Har bir tovar — bitta yozuv (count = nusxa soni). SKU = tovar id si.
  const tags = [];
  for (const [id, cnt] of st._sel) {
    const p = (st._items || []).find((x) => x.id === id);
    if (!p) continue;
    const price = p.sell_price_sum || p.price_sum || 0;
    tags.push({ sku: String(p.id), priceText: priceFmt(price), count: cnt });
  }
  if (!tags.length) { toast('Avval tovar belgilang'); return; }
  const total = tags.reduce((a, t) => a + t.count, 0);

  const r = await api.printLabels({ printer: cfg.printer, w: cfg.w, h: cfg.h, tags });
  if (r && r.ok) {
    toast(`✓ ${total} ta sennik chop etildi`);
    st._sel.clear();
    if (c) drawPriceTags(c);
  } else {
    toast(`Chop etishda xato${r && r.error ? ': ' + r.error : ''}`);
  }
}

// ── Prixod tovarlaridan sennik chop etish (modal: o'chirish + nusxa soni) ────
// Joriy prixod ro'yxatidagi (_lines) tovarlarni tayyor spiska qilib ochadi:
// keraksizini o'chirish, har biriga nusxa sonini tanlash, so'ng printerga chiqarish.
async function openPxLabelPrint() {
  const lines = (renderPrixod._lines || []).filter((l) => l && l.id != null);
  if (!lines.length) { toast('Avval ro\'yxatga tovar qo\'shing'); return; }
  const rows = lines.map((l) => ({
    sku: String(l.id),
    name: l.name || '',
    price: Number(l.narx) || 0,
    count: Math.max(1, Math.round(Number(l.miqdori) || 1)),
  }));
  return openLabelPrintModal(rows);
}

// Tarixdagi saqlangan prixod hujjatidan sennik chop etish (items: {id,name,sell,qty})
function openLabelPrintModalFromPurchase(p) {
  const rows = ((p && p.items) || [])
    .filter((it) => it && it.id != null)
    .map((it) => ({
      sku: String(it.id),
      name: it.name || '',
      price: Number(it.sell) || 0,
      count: Math.max(1, Math.round(Number(it.qty) || 1)),
    }));
  if (!rows.length) { toast('Bu prixodda SKU li tovar yo\'q'); return; }
  return openLabelPrintModal(rows);
}

// Umumiy chop-modal: rows = [{sku,name,price,count}] — o'chirish + nusxa soni + chop etish
async function openLabelPrintModal(rows) {
  if (!rows || !rows.length) { toast('Ro\'yxat bo\'sh'); return; }

  let printers = [];
  try { printers = await api.getPrinters(); } catch (_) {}
  if (!Array.isArray(printers)) printers = [];
  const S = await api.getState();
  const cfg = { printer: S.labelPrinter || '', w: S.labelW || 30, h: S.labelH || 20 };

  const old = document.getElementById('plOverlay');
  if (old) old.remove();
  const ov = document.createElement('div');
  ov.id = 'plOverlay';
  ov.className = 'np-overlay';

  const draw = () => {
    const total = rows.reduce((a, r) => a + (Number(r.count) || 0), 0);
    const prOpts = ['<option value="">— Printer tanlang —</option>'].concat(
      printers.map((p) => `<option value="${esc(p.name)}" ${p.name === cfg.printer ? 'selected' : ''}>${esc(p.display || p.name)}</option>`)
    ).join('');
    ov.innerHTML = `
      <div class="np-modal" style="max-width:700px">
        <div class="np-head"><span>🖨 Sennik chop etish — prixod tovarlari</span><button class="np-x" id="plClose">✕</button></div>
        <div class="np-body">
          <div class="filters" style="align-items:flex-end;margin-bottom:10px">
            <div class="filter" style="min-width:200px"><label>Printer</label><select id="plPrinter">${prOpts}</select></div>
            <div class="filter" style="width:84px"><label>En (mm)</label><input id="plW" type="number" min="10" step="1" value="${esc(cfg.w)}"></div>
            <div class="filter" style="width:84px"><label>Bo'yi (mm)</label><input id="plH" type="number" min="10" step="1" value="${esc(cfg.h)}"></div>
          </div>
          <div class="tbl-wrap">
            <table class="tbl">
              <thead><tr><th>№</th><th>SKU</th><th>Nomi</th><th class="num">Narx</th><th class="num" style="width:110px">Nusxa</th><th style="width:40px"></th></tr></thead>
              <tbody>
                ${rows.map((r, i) => `<tr data-i="${i}">
                  <td>${i + 1}</td><td>${esc(r.sku)}</td><td>${esc(r.name)}</td>
                  <td class="num">${money(r.price)}</td>
                  <td class="num"><input type="number" min="0" step="1" class="qty-inp pl-cnt" data-i="${i}" value="${r.count}"></td>
                  <td><button class="px-del pl-del" data-i="${i}" title="Ro'yxatdan o'chirish">✕</button></td>
                </tr>`).join('')}
              </tbody>
            </table>
          </div>
        </div>
        <div class="np-foot">
          <button class="btn-soft ghost" id="plCancel">Bekor</button>
          <button class="btn-soft" id="plPrint">🖨 Chop etish (${int(total)} ta)</button>
        </div>
      </div>`;

    const re = (id) => document.getElementById(id);
    const close = () => ov.remove();
    re('plClose').addEventListener('click', close);
    re('plCancel').addEventListener('click', close);
    ov.addEventListener('click', (e) => { if (e.target === ov) close(); });

    const saveCfg = () => { try { api.saveLabelCfg(cfg); } catch (_) {} };
    re('plPrinter').addEventListener('change', (e) => { cfg.printer = e.target.value; saveCfg(); });
    re('plW').addEventListener('change', (e) => { cfg.w = Math.max(10, parseInt(e.target.value, 10) || 30); saveCfg(); });
    re('plH').addEventListener('change', (e) => { cfg.h = Math.max(10, parseInt(e.target.value, 10) || 20); saveCfg(); });

    ov.querySelectorAll('.pl-cnt').forEach((inp) => inp.addEventListener('input', () => {
      const i = Number(inp.dataset.i);
      rows[i].count = Math.max(0, parseInt(inp.value, 10) || 0);
      const t = rows.reduce((a, r) => a + (Number(r.count) || 0), 0);
      const btn = re('plPrint'); if (btn) btn.textContent = `🖨 Chop etish (${int(t)} ta)`;
    }));
    ov.querySelectorAll('.pl-del').forEach((b) => b.addEventListener('click', () => {
      rows.splice(Number(b.dataset.i), 1);
      if (!rows.length) { close(); toast('Ro\'yxat bo\'sh'); return; }
      draw();
    }));

    re('plPrint').addEventListener('click', async () => {
      if (!cfg.printer) { toast('Avval printer tanlang'); return; }
      const tags = rows
        .filter((r) => (Number(r.count) || 0) > 0)
        .map((r) => ({ sku: r.sku, priceText: priceFmt(r.price), count: r.count }));
      if (!tags.length) { toast('Nusxa soni 0 — hech narsa tanlanmagan'); return; }
      const total = tags.reduce((a, t) => a + t.count, 0);
      const btn = re('plPrint'); btn.disabled = true; btn.textContent = 'Chop etilmoqda…';
      const rr = await api.printLabels({ printer: cfg.printer, w: cfg.w, h: cfg.h, tags });
      if (rr && rr.ok) { toast(`✓ ${total} ta sennik chop etildi`); close(); }
      else { toast(`Xato: ${rr && rr.error ? rr.error : 'chop etilmadi'}`); btn.disabled = false; btn.textContent = `🖨 Chop etish (${int(total)} ta)`; }
    });
  };

  // Avval DOM ga qo'shamiz, KEYIN draw() — draw ichida getElementById ishlashi uchun
  document.body.appendChild(ov);
  draw();
}

// ── OMBOR: Tovar qabul qilish (prixod — bir hujjatda ko'p tovar) ─────────────
async function renderPrixod(c) {
  const st = renderPrixod;
  if (!st._lines) st._lines = [];
  if (!st._view) st._view = 'history';     // default: oldingi prixodlar tarixi
  c.innerHTML = `<div class="loading">Yuklanyapti…</div>`;
  const r = await fetchAllProducts();
  if (!r.ok) {
    if (r.authRequired) return doLogout();
    c.innerHTML = `<div class="err-box">Server xatosi: ${esc(r.error || '')}</div>`;
    return;
  }
  st._items = r.items;
  if (st._view === 'create') drawPrixod(c);
  else drawPrixodHistory(c);
}

// ── Oldingi prixodlar tarixi (purchases jurnali) ─────────────────────────────
async function drawPrixodHistory(c) {
  const st = renderPrixod;
  c.innerHTML = `<div class="loading">Tarix yuklanyapti…</div>`;
  const from = st._histFrom || '', to = st._histTo || '';
  const r = await api.prixodList(from, to);
  if (!r.ok) {
    if (r.authRequired) return doLogout();
    c.innerHTML = `<div class="err-box">${esc(r.error || '')}</div>`;
    return;
  }
  const list = r.items || [], s = r.summary || {};
  c.innerHTML = `
    <div class="filters" style="align-items:center">
      <div class="filter"><label>Dan</label><input type="date" id="phFrom" value="${esc(from)}"></div>
      <div class="filter"><label>Gacha</label><input type="date" id="phTo" value="${esc(to)}"></div>
      <button class="btn-soft ghost" id="phClear">Tozalash</button>
      <div style="flex:1"></div>
      <button class="btn-soft" id="phNew">➕ Yangi qabul (prixod)</button>
    </div>
    <div class="ph-sum">📦 <b>${int(s.count || 0)}</b> ta prixod · Umumiy tannarx: <b>${money(s.total_cost || 0)}</b> · Umumiy narx: <b>${money(s.total_sell || 0)}</b> · Natsenka: <b>${s.markup || 0}%</b></div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th>№</th><th>Sana</th><th>Xodim</th><th class="num">Tovar</th><th class="num">Umumiy tannarx</th><th class="num">Umumiy narx</th><th class="num">Natsenka</th></tr></thead>
        <tbody>
          ${list.length ? list.map((p) => phRowHtml(p)).join('')
            : `<tr><td colspan="7"><div class="chart-empty">Hali prixod yo'q — «Yangi qabul» bilan boshlang. Saqlangan har prixod shu yerda ko'rinadi.</div></td></tr>`}
        </tbody>
      </table>
    </div>`;
  const re = (id) => document.getElementById(id);
  re('phNew').addEventListener('click', () => { st._view = 'create'; drawPrixod(c); });
  re('phClear').addEventListener('click', () => { st._histFrom = ''; st._histTo = ''; drawPrixodHistory(c); });
  re('phFrom').addEventListener('change', (e) => { st._histFrom = e.target.value; drawPrixodHistory(c); });
  re('phTo').addEventListener('change', (e) => { st._histTo = e.target.value; drawPrixodHistory(c); });
  c.querySelectorAll('.ph-row').forEach((row) => row.addEventListener('click', () => {
    c.querySelectorAll('.ph-det-' + row.dataset.id).forEach((el) => el.classList.toggle('hidden'));
    row.classList.toggle('open');
  }));
  // Tarixdagi prixod tovarlaridan sennik chop etish
  c.querySelectorAll('.ph-print').forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const p = list.find((x) => String(x.id) === String(b.dataset.pid));
    if (p) openLabelPrintModalFromPurchase(p);
  }));
}

function phRowHtml(p) {
  const markup = (p.total_cost > 0) ? Math.round((p.total_sell - p.total_cost) / p.total_cost * 100) : 0;
  const det = (p.items || []).map((it) => `<tr class="ph-det ph-det-${p.id} hidden">
      <td></td><td colspan="2" class="ph-det-name">${esc(it.name || '')}</td>
      <td class="num">${money(it.qty)} ${esc(it.unit || '')}</td>
      <td class="num">${money(it.cost)}</td><td class="num">${money(it.sell)}</td><td></td></tr>`).join('');
  // Kengaytirilgan detalda — shu prixod tovarlaridan sennik chop etish tugmasi
  const printRow = `<tr class="ph-det ph-det-${p.id} hidden ph-print-row">
      <td colspan="7" style="text-align:right">
        <button class="btn-soft ph-print" data-pid="${p.id}">🖨 Sennik chop et</button></td></tr>`;
  return `<tr class="ph-row" data-id="${p.id}" title="Tovarlarni ko'rish">
      <td>#${p.id}</td><td>${esc(p.created_at || '')}</td><td>${esc(p.employee_name || '')}</td>
      <td class="num">${int(p.item_count || 0)}</td><td class="num">${money(p.total_cost || 0)}</td>
      <td class="num">${money(p.total_sell || 0)}</td><td class="num">${markup}%</td>
    </tr>${det}${printRow}`;
}

function pxNextSku() {
  const items = renderPrixod._items || [];
  const max = items.reduce((m, p) => Math.max(m, Number(p.id) || 0), 9999);
  return max + 1;
}

function drawPrixod(c) {
  const st = renderPrixod;
  if (!st._currency) st._currency = 'sum';
  if (st._rate == null) st._rate = Number(S.usdRate) || 12500;
  const usd = st._currency === 'usd';
  const lines = st._lines;
  const totalQty = lines.reduce((a, l) => a + (Number(l.miqdori) || 0), 0);
  const totalCost = lines.reduce((a, l) => a + (Number(l.miqdori) || 0) * (Number(l.tannarx) || 0), 0);

  c.innerHTML = `
    <div class="filters" style="align-items:center">
      <div class="filter"><label>Sana</label><span>${esc(ymd(tashkentNow()))}</span></div>
      <div class="filter"><label>Valyuta (kiritish)</label>
        <button class="btn-soft px-cur${usd ? ' usd' : ''}" id="pxCur" title="So'm ⇄ Dollar — bossangiz almashadi">${usd ? '💲 Dollar' : "💵 So'm"}</button></div>
      ${usd ? `<div class="filter" style="width:140px"><label>Kurs (1$ = so'm)</label>
        <input id="pxRate" type="number" min="1" step="any" value="${esc(st._rate)}"></div>` : ''}
      <div class="filter" style="flex:1; min-width:260px; position:relative">
        <label>Tovar qidirib qo'shish</label>
        <input id="pxSearch" type="text" placeholder="Nomi · SKU · barcode bo'yicha qidiring" autocomplete="off">
        <div id="pxResults" class="px-results hidden"></div>
      </div>
      <button class="btn-soft ghost" id="pxBack">← Tarix</button>
      <input type="file" id="pxPhoto" accept="image/*" hidden>
      <button class="btn-soft px-ai" id="pxScan">📷 Rasmdan to'ldirish (AI)</button>
      <button class="btn-soft ghost" id="pxNew">➕ Yangi tovar</button>
      <button class="btn-soft ghost" id="pxPrintLbl">🖨 Sennik chop et</button>
      <button class="btn-soft" id="pxSave">💾 Saqlash (${int(lines.length)} tovar)</button>
    </div>

    ${pxScanSummaryHtml()}
    ${pxReviewHtml()}

    <div class="tbl-wrap">
      <table class="tbl px-tbl">
        <thead><tr>
          <th>№</th><th>Artikul</th><th>Nomi</th><th class="num">Qoldiq</th><th>O'lchov</th>
          <th class="num">Miqdori</th><th class="num">Eski tannarx</th><th class="num">Tannarxi${usd ? ' ($)' : " (so'm)"}</th><th class="num">Narxi</th><th class="num">Jami</th><th></th>
        </tr></thead>
        <tbody id="pxBody">
          ${lines.length ? lines.map((l, i) => pxRowHtml(l, i)).join('')
            : `<tr><td colspan="11"><div class="chart-empty">Qabul qilish uchun tovarlarni yuqoridan qidirib qo'shing</div></td></tr>`}
        </tbody>
        ${lines.length ? `<tfoot><tr class="px-foot">
          <td colspan="5" class="num"><b>Jami:</b></td>
          <td class="num"><b>${money(totalQty)}</b></td>
          <td></td><td></td><td></td>
          <td class="num"><b id="pxTotCost">${money(totalCost)}</b></td><td></td>
        </tr></tfoot>` : ''}
      </table>
    </div>
    <div id="pxMsg" class="px-msg" style="margin-top:12px"></div>`;

  const re = (id) => document.getElementById(id);

  // Qidiruv → natijalar ro'yxati
  const search = re('pxSearch');
  const results = re('pxResults');
  const renderResults = () => {
    const q = (search.value || '').trim().toLowerCase();
    if (!q) { results.classList.add('hidden'); results.innerHTML = ''; return; }
    const hits = (st._items || []).filter((p) =>
      (p.name || '').toLowerCase().includes(q) || String(p.id).includes(q) || (p.barcode || '').includes(q)
    ).slice(0, 8);
    results.innerHTML = hits.length
      ? hits.map((p) => `<div class="px-res" data-id="${p.id}">
          <span><b>${esc(p.name)}</b> <small>SKU ${esc(p.id)}</small></span>
          <span class="num">${money(p.qty)} ${esc(p.unit || '')}</span></div>`).join('')
      : `<div class="px-res-empty">Topilmadi — «Yangi tovar» tugmasidan qo'shing</div>`;
    results.classList.remove('hidden');
    results.querySelectorAll('.px-res').forEach((row) => row.addEventListener('click', () => {
      pxAddLine(Number(row.dataset.id), c); search.value = ''; results.classList.add('hidden'); search.focus();
    }));
  };
  search.addEventListener('input', renderResults);
  search.addEventListener('focus', renderResults);
  document.addEventListener('click', (e) => {
    if (!results.contains(e.target) && e.target !== search) results.classList.add('hidden');
  }, { once: true });

  // Qator inputlari (miqdor/tannarx/narx) → modelni yangilab Jami/footerni qayta hisoblash
  c.querySelectorAll('.px-inp').forEach((inp) => inp.addEventListener('input', () => {
    const i = Number(inp.dataset.i), field = inp.dataset.f;
    if (field === 'tannarx' && st._currency === 'usd') {
      // Input USD da kiritiladi — modelga so'mga aylantirib saqlaymiz (kursga ko'paytirib)
      const rate = Number(st._rate) || 12500;
      st._lines[i].tannarx = (parseFloat(inp.value) || 0) * rate;
    } else {
      st._lines[i][field] = inp.value;
    }
    const ln = st._lines[i];
    const rowJami = c.querySelector(`#pxJami-${i}`);
    if (rowJami) rowJami.textContent = money((Number(ln.miqdori) || 0) * (Number(ln.tannarx) || 0));
    if (field === 'tannarx') {
      // USD rejimidagi "= … so'm" maslahatini va eski↔yangi farq rangini yangilaymiz
      const hint = document.getElementById(`pxCurHint-${i}`);
      if (hint) hint.textContent = `= ${money(Number(ln.tannarx) || 0)} so'm`;
      const dd = document.getElementById(`pxDiff-${i}`);
      if (dd && ln._oldCost != null) dd.innerHTML = pxDiffHtml(ln._oldCost, Number(ln.tannarx) || 0);
    }
    const tot = st._lines.reduce((a, l) => a + (Number(l.miqdori) || 0) * (Number(l.tannarx) || 0), 0);
    const tc = re('pxTotCost'); if (tc) tc.textContent = money(tot);
  }));
  c.querySelectorAll('.px-del').forEach((b) => b.addEventListener('click', () => { st._lines.splice(Number(b.dataset.i), 1); drawPrixod(c); }));

  re('pxBack').addEventListener('click', () => { renderPrixod._view = 'history'; renderPrixod(c); });
  re('pxNew').addEventListener('click', () => openNewProductModal(c));
  re('pxPrintLbl').addEventListener('click', () => openPxLabelPrint());
  re('pxSave').addEventListener('click', () => pxConfirmSave(c));

  // Valyuta tugmasi (So'm ⇄ Dollar) va kurs
  re('pxCur').addEventListener('click', () => {
    st._currency = (st._currency === 'usd') ? 'sum' : 'usd';
    drawPrixod(c);
  });
  const pxRateInp = re('pxRate');
  if (pxRateInp) pxRateInp.addEventListener('change', () => {
    st._rate = Math.max(1, parseFloat(pxRateInp.value) || st._rate);
    drawPrixod(c);
  });

  // AI: rasmdan to'ldirish
  re('pxScan').addEventListener('click', () => re('pxPhoto').click());
  re('pxPhoto').addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    if (f) pxScanFile(f, c);
  });
  // Tekshirish (review) qatorlari
  c.querySelectorAll('.px-rv-ok').forEach((b) => b.addEventListener('click', () => pxReviewConfirm(Number(b.dataset.i), c)));
  c.querySelectorAll('.px-rv-skip').forEach((b) => b.addEventListener('click', () => {
    renderPrixod._review.splice(Number(b.dataset.i), 1); drawPrixod(c);
  }));
}

function pxNorm(s) { return String(s || '').toLowerCase().replace(/\s+/g, ' ').trim(); }

function pxDiffHtml(oldC, newC) {
  oldC = Number(oldC) || 0; newC = Number(newC) || 0;
  if (!oldC) return newC ? `<div class="px-diff new">yangi tovar</div>` : '';
  if (newC === oldC) return `<div class="px-diff same">narx o'zgarmadi</div>`;
  const pct = Math.round((newC - oldC) / oldC * 100);
  const up = newC > oldC;            // qimmatladi → qizil, arzonladi → yashil
  return `<div class="px-diff ${up ? 'up' : 'down'}">${up ? '▲ qimmatladi +' + pct : '▼ arzonladi ' + pct}%</div>`;
}

function pxRowHtml(l, i) {
  const st = renderPrixod;
  const usd = st._currency === 'usd';
  const rate = Number(st._rate) || 12500;
  const jami = (Number(l.miqdori) || 0) * (Number(l.tannarx) || 0);
  const conf = (l._conf != null)
    ? `<span class="px-conf ${l._conf >= 0.8 ? 'ok' : 'mid'}" title="AI moslik ishonchi">${Math.round(l._conf * 100)}%</span> `
    : '';
  const rawHint = (l._rawName && pxNorm(l._rawName) !== pxNorm(l.name))
    ? `<div class="px-raw" title="Nakladnoyda shunday yozilgan">🧾 ${esc(l._rawName)}</div>` : '';
  const diff = (l._oldCost != null) ? pxDiffHtml(l._oldCost, Number(l.tannarx) || 0) : '';
  // Eski tannarx (o'zgarmas, ma'lumot uchun); yangi tovar bo'lsa "—"
  const eski = (Number(l._oldCost) > 0) ? money(l._oldCost) : '—';
  // Tannarx inputi joriy valyutada ko'rsatiladi; model esa har doim so'mda saqlanadi
  const tannarxVal = usd
    ? (Number(l.tannarx) ? +(Number(l.tannarx) / rate).toFixed(4) : '')
    : l.tannarx;
  const sumHint = usd
    ? `<div class="px-cur-hint" id="pxCurHint-${i}">= ${money(Number(l.tannarx) || 0)} so'm</div>` : '';
  return `<tr${l._conf != null ? ' class="px-scanned"' : ''}>
    <td>${i + 1}</td>
    <td>${esc(l.id)}</td>
    <td>${conf}${esc(l.name)}${rawHint}</td>
    <td class="num">${money(l.qty)}</td>
    <td>${esc(l.unit || 'dona')}</td>
    <td class="num"><input class="qty-inp px-inp" type="number" min="0" step="any" data-i="${i}" data-f="miqdori" value="${l.miqdori}"></td>
    <td class="num px-eski">${eski}</td>
    <td class="num"><input class="qty-inp px-inp" type="number" min="0" step="any" data-i="${i}" data-f="tannarx" value="${tannarxVal}">${sumHint}<span id="pxDiff-${i}">${diff}</span></td>
    <td class="num"><input class="qty-inp px-inp" type="number" min="0" step="any" data-i="${i}" data-f="narx" value="${l.narx}"></td>
    <td class="num"><b id="pxJami-${i}">${money(jami)}</b></td>
    <td><button class="px-del" data-i="${i}" title="O'chirish">✕</button></td>
  </tr>`;
}

function pxAddLine(id, c) {
  const st = renderPrixod;
  if (st._lines.some((l) => l.id === id)) { toast('Bu tovar allaqachon qo\'shilgan'); return; }
  const p = (st._items || []).find((x) => x.id === id);
  if (!p) return;
  st._lines.push({
    id: p.id, name: p.name, unit: p.unit || 'dona', qty: p.qty || 0,
    barcode: p.barcode || '', wholesale: p.wholesale_sum || 0,
    miqdori: 1, tannarx: p.cost_price_sum || 0, narx: p.sell_price_sum || p.price_sum || 0,
    _oldCost: Number(p.cost_price_sum) || 0,   // shu prixoddan OLDINGI tannarx (o'zgarmas)
  });
  drawPrixod(c);
}

// ── AI: nakladnoy rasmidan to'ldirish ────────────────────────────────────────
async function pxScanFile(file, c) {
  const ov = document.createElement('div');
  ov.className = 'px-scan-ov';
  ov.innerHTML = `<div class="px-scan-box"><div class="px-spin"></div>
    <div>🧾 AI nakladnoyni o'qiyapti…<br><small>${esc(file.name || '')} — biroz kuting</small></div></div>`;
  document.body.appendChild(ov);
  try {
    const buf = new Uint8Array(await file.arrayBuffer());
    const r = await api.prixodScan(buf, file.name || 'nakladnoy.jpg');
    ov.remove();
    if (!r.ok) {
      if (r.authRequired) return doLogout();
      toast('⚠️ ' + (r.error || 'AI nakladnoyni o\'qiy olmadi'));
      return;
    }
    applyScanResult(r, c);
  } catch (e) {
    ov.remove();
    toast('⚠️ Xato: ' + String(e && e.message || e));
  }
}

function applyScanResult(r, c) {
  const st = renderPrixod;
  if (!st._lines) st._lines = [];
  if (!st._review) st._review = [];
  let added = 0, dup = 0;
  (r.items || []).forEach((it) => {
    if (it.match) {
      const m = it.match;
      if (st._lines.some((l) => l.id === m.id)) { dup++; return; }
      st._lines.push({
        id: m.id, name: m.name, unit: m.unit || it.unit || 'dona', qty: m.qty || 0,
        barcode: m.barcode || '', wholesale: m.wholesale_sum || 0,
        miqdori: it.qty || 0, tannarx: it.new_price || m.cost_price_sum || 0,
        narx: m.sell_price_sum || 0,
        _oldCost: m.cost_price_sum || 0, _conf: m.confidence, _rawName: it.raw_name,
      });
      added++;
    } else {
      st._review.push({
        raw_name: it.raw_name, qty: it.qty, unit: it.unit,
        new_price: it.new_price, candidates: it.candidates || [],
      });
    }
  });
  st._scanInfo = { count: r.count || 0, matched: r.matched || 0, unmatched: r.unmatched || 0 };
  drawPrixod(c);
  toast(`🧾 ${added} ta avtomat qo'shildi` +
    (st._review.length ? `, ${st._review.length} ta tekshirish kerak` : '') +
    (dup ? ` (${dup} ta avval bor edi)` : ''));
}

function pxScanSummaryHtml() {
  const s = renderPrixod._scanInfo;
  if (!s) return '';
  return `<div class="px-scan-sum">🧾 AI o'qidi: <b>${int(s.count)}</b> qator ·
    <b class="ok">${int(s.matched)}</b> mos keldi ·
    <b class="mid">${int(s.unmatched)}</b> tekshirish kerak</div>`;
}

function pxReviewHtml() {
  const rv = renderPrixod._review || [];
  if (!rv.length) return '';
  return `<div class="px-review">
    <div class="px-review-h">⚠️ Tekshirish kerak — bular katalogdan aniq topilmadi. Tanlang yoki «Yangi tovar» qiling:</div>
    ${rv.map((it, i) => pxReviewRow(it, i)).join('')}
  </div>`;
}

function pxReviewRow(it, i) {
  const opts = (it.candidates || []).map((cnd, k) =>
    `<option value="${k}">${esc(cnd.name)} — ${Math.round((cnd.confidence || 0) * 100)}%</option>`).join('');
  return `<div class="px-rv-row">
    <div class="px-rv-name">🧾 <b>${esc(it.raw_name)}</b>
      <small>${money(it.qty)} ${esc(it.unit || '')} · ${money(it.new_price)} so'm</small></div>
    <select class="px-rv-sel" data-i="${i}">
      <option value="">— katalogdan tanlang —</option>
      ${opts}
      <option value="new">➕ Yangi tovar sifatida</option>
    </select>
    <button class="btn-soft px-rv-ok" data-i="${i}">✓ Qo'shish</button>
    <button class="px-del px-rv-skip" data-i="${i}" title="Tashlab ketish">✕</button>
  </div>`;
}

function pxReviewConfirm(i, c) {
  const st = renderPrixod;
  const it = st._review[i];
  if (!it) return;
  const sel = c.querySelector(`.px-rv-sel[data-i="${i}"]`);
  const val = sel ? sel.value : '';
  if (val === '') { toast('Avval katalogdan tanlang yoki «Yangi tovar»'); return; }
  if (val === 'new') {
    st._review.splice(i, 1);
    drawPrixod(c);
    openNewProductModal(c, { name: it.raw_name, cost: it.new_price, sell: it.new_price, unit: it.unit, qty: it.qty });
    return;
  }
  const cnd = (it.candidates || [])[Number(val)];
  if (!cnd) return;
  if (!st._lines.some((l) => l.id === cnd.id)) {
    st._lines.push({
      id: cnd.id, name: cnd.name, unit: cnd.unit || it.unit || 'dona', qty: cnd.qty || 0,
      barcode: cnd.barcode || '', wholesale: cnd.wholesale_sum || 0,
      miqdori: it.qty || 0, tannarx: it.new_price || cnd.cost_price_sum || 0,
      narx: cnd.sell_price_sum || 0,
      _oldCost: cnd.cost_price_sum || 0, _conf: cnd.confidence, _rawName: it.raw_name,
    });
  } else {
    toast('Bu tovar allaqachon qatorda bor');
  }
  st._review.splice(i, 1);
  drawPrixod(c);
}

// Saqlashdan oldin tasdiqlash: xulosa (tovar soni, umumiy miqdor/tannarx/narx) + izoh
function pxConfirmSave(c) {
  const st = renderPrixod;
  const lines = st._lines.filter((l) => (Number(l.miqdori) || 0) > 0);
  if (!lines.length) { toast('Kamida bitta tovarga miqdor kiriting'); return; }
  const totalQty = lines.reduce((a, l) => a + (Number(l.miqdori) || 0), 0);
  const totalCost = lines.reduce((a, l) => a + (Number(l.miqdori) || 0) * (Number(l.tannarx) || 0), 0);
  const totalSell = lines.reduce((a, l) => a + (Number(l.miqdori) || 0) * (Number(l.narx) || 0), 0);

  const old = document.getElementById('pcOverlay');
  if (old) old.remove();
  const ov = document.createElement('div');
  ov.id = 'pcOverlay';
  ov.className = 'np-overlay';
  ov.innerHTML = `
    <div class="np-modal" style="max-width:460px">
      <div class="np-head"><span>📦 Prixodni saqlash</span><button class="np-x" id="pcClose">✕</button></div>
      <div class="np-body">
        <div class="pc-sum">
          <div><span>Tovar turlari</span><b>${int(lines.length)} xil</b></div>
          <div><span>Umumiy miqdor</span><b>${money(totalQty)}</b></div>
          <div><span>Umumiy tannarx</span><b>${money(totalCost)} so'm</b></div>
          <div><span>Umumiy sotuv narxi</span><b>${money(totalSell)} so'm</b></div>
        </div>
        <label class="np-l">Izoh (ixtiyoriy)</label>
        <input id="pcNote" class="np-inp" type="text" placeholder="masalan: yetkazib beruvchi nomi" value="${esc(st._note || '')}">
        <div class="pc-warn">⚠️ Saqlangach tovarlar qoldig'iga qo'shiladi va tannarx/narxlar yangilanadi.</div>
      </div>
      <div class="np-foot">
        <button class="btn-soft ghost" id="pcCancel">Bekor</button>
        <button class="btn-soft" id="pcOk">✓ Ha, saqlash</button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  const re = (id) => document.getElementById(id);
  const close = () => ov.remove();
  re('pcClose').addEventListener('click', close);
  re('pcCancel').addEventListener('click', close);
  ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
  const confirm = () => { st._note = re('pcNote').value.trim(); close(); pxSave(c); };
  re('pcOk').addEventListener('click', confirm);
  re('pcNote').addEventListener('keydown', (e) => { if (e.key === 'Enter') confirm(); });
  re('pcNote').focus();
}

async function pxSave(c) {
  const st = renderPrixod;
  const msg = document.getElementById('pxMsg');
  const lines = st._lines.filter((l) => (Number(l.miqdori) || 0) > 0);
  if (!lines.length) { msg.className = 'px-msg err'; msg.textContent = 'Kamida bitta tovarga miqdor kiriting'; return; }
  const btn = document.getElementById('pxSave');
  btn.disabled = true; btn.textContent = 'Saqlanyapti…';
  // Butun hujjatni bitta endpointga yuboramiz — qoldiq+narx yangilanadi VA tarixga yoziladi.
  const payload = {
    note: (st._note || ''),
    source: 'desktop-admin',
    lines: lines.map((l) => ({
      id: l.id, name: l.name, unit: l.unit,
      qty: Number(l.miqdori) || 0, cost: Number(l.tannarx) || 0,
      sell: Number(l.narx) || 0, wholesale: Number(l.wholesale) || 0,
    })),
  };
  const r = await api.prixodSave(payload);
  btn.disabled = false;
  if (!r.ok) {
    if (r.authRequired) return doLogout();
    msg.className = 'px-msg err'; msg.textContent = r.error || 'Xatolik';
    return;
  }
  toast(`✓ Prixod #${r.id} saqlandi (${r.saved} tovar qabul qilindi)`);
  st._lines = [];
  st._review = [];
  st._scanInfo = null;
  st._view = 'history';          // saqlangach tarixga qaytamiz (yangi prixod ro'yxatda ko'rinadi)
  await renderPrixod(c);
}

// Yangi tovar yaratish modal'i (avtomat SKU, kerakmas maydonlarsiz)
// prefill — AI prixod review'dan keladi: { name, cost, sell, unit, qty }
function openNewProductModal(c, prefill) {
  prefill = prefill || {};
  const old = document.getElementById('npOverlay');
  if (old) old.remove();
  const ov = document.createElement('div');
  ov.id = 'npOverlay';
  ov.className = 'np-overlay';
  ov.innerHTML = `
    <div class="np-modal">
      <div class="np-head"><span>➕ Yangi tovar</span><button class="np-x" id="npClose">✕</button></div>
      <div class="np-body">
        <div class="np-sku">Artikul (SKU): <b>${int(pxNextSku())}</b> <small>— saqlaganda avtomat beriladi</small></div>
        <label class="np-l">Nomi *</label>
        <input id="npName" class="np-inp" type="text" placeholder="Tovar nomi" autofocus>
        <div class="np-2">
          <div><label class="np-l">O'lchov</label>
            <select id="npUnit" class="np-inp">
              <option value="dona">Dona</option><option value="kg">Kilogramm</option>
              <option value="litr">Litr</option><option value="metr">Metr</option><option value="upak">Upakovka</option>
            </select></div>
          <div><label class="np-l">Barkod</label><input id="npBarcode" class="np-inp" type="text" placeholder="ixtiyoriy"></div>
        </div>
        <div class="np-3">
          <div><label class="np-l">Tannarx (so'm)</label><input id="npCost" class="np-inp" type="number" min="0" step="any" placeholder="0"></div>
          <div><label class="np-l">Sotuv narxi (so'm)</label><input id="npSell" class="np-inp" type="number" min="0" step="any" placeholder="0"></div>
          <div><label class="np-l">Ulgurji (so'm)</label><input id="npWhs" class="np-inp" type="number" min="0" step="any" placeholder="0"></div>
        </div>
        <div id="npMsg" class="px-msg"></div>
      </div>
      <div class="np-foot">
        <button class="btn-soft ghost" id="npCancel">Bekor</button>
        <button class="btn-soft" id="npSave">Saqlash va qo'shish</button>
      </div>
    </div>`;
  document.body.appendChild(ov);

  const re = (id) => document.getElementById(id);
  const close = () => ov.remove();
  re('npClose').addEventListener('click', close);
  re('npCancel').addEventListener('click', close);
  ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
  // AI prixoddan kelgan qiymatlarni oldindan to'ldiramiz
  if (prefill.name) re('npName').value = prefill.name;
  if (prefill.unit) re('npUnit').value = prefill.unit;
  if (prefill.cost) re('npCost').value = prefill.cost;
  if (prefill.sell) re('npSell').value = prefill.sell;
  re('npName').focus();

  re('npSave').addEventListener('click', async () => {
    const name = re('npName').value.trim();
    const msg = re('npMsg');
    if (!name) { msg.className = 'px-msg err'; msg.textContent = 'Nomini kiriting'; return; }
    const btn = re('npSave'); btn.disabled = true; btn.textContent = 'Saqlanyapti…';
    const prod = {
      id: 0, name, unit: re('npUnit').value, barcode: re('npBarcode').value.trim(),
      cost_price_sum: parseFloat(re('npCost').value) || 0,
      sell_price_sum: parseFloat(re('npSell').value) || 0,
      wholesale_sum: parseFloat(re('npWhs').value) || 0,
      qty: 0,
    };
    const r = await api.productSave(prod);
    btn.disabled = false; btn.textContent = 'Saqlash va qo\'shish';
    if (!r.ok) { if (r.authRequired) return doLogout(); msg.className = 'px-msg err'; msg.textContent = r.error || 'Xatolik'; return; }
    const np = r.product || { id: r.id, name, unit: prod.unit, qty: 0, barcode: prod.barcode,
      cost_price_sum: prod.cost_price_sum, sell_price_sum: prod.sell_price_sum, wholesale_sum: prod.wholesale_sum };
    renderPrixod._items.push(np);     // keshga qo'shamiz
    close();
    pxAddLine(np.id, c);              // qabul qilish jadvaliga qator sifatida qo'shamiz
    const newLn = renderPrixod._lines.find((l) => l.id === np.id);
    if (newLn) {
      newLn._oldCost = 0;             // yangi tovar — eski narx yo'q (diff "yangi tovar")
      if (prefill.qty) newLn.miqdori = prefill.qty;
      if (prefill.cost) newLn.tannarx = prefill.cost;
      if (prefill.name) newLn._rawName = prefill.name;
      drawPrixod(c);
    }
    toast(`✓ «${name}» yaratildi (SKU ${np.id})`);
  });
}

// ── Hodisalar ────────────────────────────────────────────────────────────────
$('loginBtn').addEventListener('click', doLogin);
$('passInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
$('loginInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('passInput').focus(); });
$('logoutBtn').addEventListener('click', doLogout);

// Logo'ni asset bridge orqali ham yuklaymiz (build'da yo'l o'zgarsa)
api.getLogo().then((d) => {
  if (!d) return;
  document.querySelectorAll('img[src="logo.png"]').forEach((img) => { img.src = d; });
});

boot();
