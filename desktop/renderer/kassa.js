'use strict';
// Chinor Kassa — renderer (UI). Ma'lumotlar main process orqali (window.kassa).

const $ = (id) => document.getElementById(id);
const nf = (n) => Math.round(Number(n) || 0).toLocaleString('ru-RU');

let CATALOG = [];
let CLIENTS = [];
let CART = [];            // [{product_id, name, sku, barcode, qty, price_sum, orig, unit, qoldiq}]
let selIndex = -1;        // tanlangan savat qatori
let selClient = null;     // {id, name}
let keypadMode = 'qty';   // 'qty' | 'price' | 'discount'
let payMethod = 'cash';   // 'cash' | 'card' | 'click'
let USD_RATE = 12500;
let editIndex = -1;       // tahrirlanayotgan savat qatori
let checksTab = 'sales';
let overrideTotal = null; // umumiy summa qo'lda o'zgartirilgan bo'lsa
let TABS = [{ items: [], client: null, override: null }]; // ochiq cheklar
let activeTab = 0;
let persistTabsTimer = null;
let LOGO = '';            // chek logosi (base64)
let bindMode = false;     // shtrix-kod biriktirish rejimi yoniqmi
let bindTarget = null;    // shtrix biriktiriladigan tanlangan mahsulot (CATALOG elementi)
let printOnSell = true;   // sotuvda avtomatik chek chop etish

// Raqamli klaviatura buferi (10s pauzadan keyin yangidan boshlanadi)
let kpBuffer = '';
let kpLast = 0;
const KP_RESET_MS = 10000;

// ── Yordamchilar ──────────────────────────────────────────────────────
function priceSum(p) {
  let s = Number(p.sell_price_sum) || 0;
  if (s <= 0 && p.sell_price_usd) s = Math.round(Number(p.sell_price_usd) * USD_RATE);
  return s;
}
function wholesaleSum(p) {
  let s = Number(p.wholesale_sum) || 0;
  if (s <= 0 && p.wholesale_usd) s = Math.round(Number(p.wholesale_usd) * USD_RATE);
  return s;
}
function matchProducts(q) {
  q = (q || '').trim().toLowerCase();
  if (!q) return CATALOG;
  return CATALOG.filter((p) =>
    (p.name || '').toLowerCase().includes(q) ||
    String(p.id).includes(q) ||
    String(p.barcode || '').includes(q));
}
function byBarcode(code) {
  code = String(code || '').trim();
  if (!code) return null;
  return CATALOG.find((x) => String(x.barcode || '').trim() === code) || null;
}
function escapeHtml(s) {
  return String(s || '').replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function openModal(id) { $(id).classList.remove('hidden'); }
function closeModal(id) { $(id).classList.add('hidden'); }
function anyModalOpen() { return !!document.querySelector('.modal-overlay:not(.hidden)'); }
function focusScan() { const s = $('scanInput'); if (s) setTimeout(() => s.focus(), 0); }

// ── Holatni qo'llash ──────────────────────────────────────────────────
function applyState(st) {
  if (!st) return;
  window._lastState = st;   // chek/kassa raqami va kassir nomi shu yerdan o'qiladi
  USD_RATE = st.usdRate || USD_RATE;
  if (!st.loggedIn) {
    $('kassaScreen').classList.add('hidden');
    $('loginScreen').classList.remove('hidden');
    if (st.serverUrl) $('serverInput').value = st.serverUrl;
    if (st.kassaNo && $('kassaInput')) $('kassaInput').value = st.kassaNo;
    return;
  }
  $('loginScreen').classList.add('hidden');
  $('kassaScreen').classList.remove('hidden');

  const net = $('netBadge');
  if (st.online) { net.textContent = '● Online'; net.className = 'badge badge-on'; }
  else { net.textContent = '● Offline'; net.className = 'badge badge-off'; }

  const pb = $('pendingBadge');
  if (st.pendingCount > 0) { pb.textContent = `⏳ ${st.pendingCount}`; pb.classList.remove('hidden'); }
  else pb.classList.add('hidden');

  // Yangilanish tugmasi
  const ub = $('updateBtn');
  const u = st.update || {};
  const icoDownload = '<svg class="ico" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  const icoCheck = '<svg class="ico" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>';
  if (u.downloaded) {
    ub.innerHTML = icoCheck + 'O\'rnatish';
    ub.className = 'btn-update ready'; ub.dataset.mode = 'install'; ub.classList.remove('hidden');
  } else if (u.downloading) {
    ub.innerHTML = icoDownload + `Yuklanmoqda ${u.progress || 0}%`;
    ub.className = 'btn-update'; ub.dataset.mode = 'progress'; ub.classList.remove('hidden');
  } else if (u.available) {
    ub.innerHTML = icoDownload + `Yangilanish bor (v${u.version})`;
    ub.className = 'btn-update'; ub.dataset.mode = 'download'; ub.classList.remove('hidden');
  } else {
    ub.classList.add('hidden');
  }

  // Smena tugmasi holati
  const sbl = $('shiftBtnLabel');
  if (sbl) {
    const sh = st.shift || {};
    sbl.textContent = sh.open ? 'Smena ✓' : 'Smena';
    $('shiftBtn').classList.toggle('shift-on', !!sh.open);
    $('shiftBtn').title = sh.open
      ? `Smena ochiq · Kassada ~${nf(sh.expectedCash || 0)} so'm`
      : 'Smena yopiq';
  }

  $('stCashier').textContent = (st.cashier ? `Hodim: ${st.cashier.name} · Filial: Chinor · Kassa-1` : '')
    + (st.appVersion ? ` · v${st.appVersion}` : '');
  const sy = $('stSync');
  if (st.syncing) sy.textContent = 'Sinxronlanmoqda…';
  else if (st.lastError && !st.online) sy.textContent = '⚠ ' + st.lastError;
  else if (st.lastSync) sy.textContent = 'Sinxron: ' + new Date(st.lastSync).toLocaleTimeString('ru-RU');
  else sy.textContent = '';

  // Kassa qulfi: qulflangan bo'lsa lock-ekran (allaqachon ko'rinayotgan bo'lsa qayta tiklamaymiz)
  if (st.locked) { if ($('lockScreen').classList.contains('hidden')) showLock(st); }
  else hideLock();
}

// ── Klient ────────────────────────────────────────────────────────────
async function loadClients() { CLIENTS = await window.kassa.getClients(); }
function updateClientChip() {
  const chip = $('clientChip');
  if (selClient) {
    const icon = selClient.is_internal ? '🏠 ' : (selClient.allow_credit ? '🤝 ' : '👤 ');
    chip.textContent = icon + selClient.name;
    chip.classList.remove('hidden');
  } else chip.classList.add('hidden');
}
function openClientModal() {
  $('clientSearch').value = '';
  renderClientList('');
  openModal('clientModal');
  setTimeout(() => $('clientSearch').focus(), 60);
}
function renderClientList(q) {
  q = (q || '').trim().toLowerCase();
  const list = (q ? CLIENTS.filter((c) => (c.name || '').toLowerCase().includes(q)) : CLIENTS).slice(0, 300);
  const box = $('clientList');
  if (!list.length) { box.innerHTML = `<div class="modal-empty">Klient topilmadi</div>`; return; }
  box.innerHTML = list.map((c) => {
    const badge = c.is_internal ? '🏠 rasxod'
      : (c.allow_credit ? '🤝 qarzga'
      : (c.debt_sum > 0 ? ('Qarz: ' + nf(c.debt_sum)) : ''));
    return `<div class="modal-row" data-id="${c.id}">
    <span>${escapeHtml(c.name)}</span>
    <span class="mr-sub">${badge}</span>
  </div>`;
  }).join('');
  box.querySelectorAll('.modal-row').forEach((el) =>
    el.addEventListener('click', () => {
      const c = CLIENTS.find((x) => x.id === Number(el.dataset.id));
      if (c) pickClient(c);
    }));
}
function pickClient(c) {
  selClient = { id: c.id, name: c.name, allow_credit: !!c.allow_credit, is_internal: !!c.is_internal };
  updateClientChip(); closeModal('clientModal');
  toast((selClient.is_internal ? '🏠 ' : '👤 ') + c.name); focusScan();
}
function clearClient() { selClient = null; updateClientChip(); closeModal('clientModal'); focusScan(); }

// To'lov turini standart holatga (Naqd) qaytaradi — har sotuvdan keyin
function resetPayMethod() {
  payMethod = 'cash';
  document.querySelectorAll('.pay-toggle').forEach((x) =>
    x.classList.toggle('active', x.dataset.pay === 'cash'));
}

// ── Cheklar — butun oynali (Telegram uslubi) ────────────────────────────
let rcSales = [];            // ko'rsatilayotgan (filtrlangan) cheklar
let rcAllSales = [];         // serverdan/lokaldan kelgan barcha cheklar (filtrsiz)
let rcDrafts = [];
let rcSelKey = null;
// Eski cheklarni qidirish filtri: matn (chek№/SKU/nomi/klient), oy (YYYY-MM), sana (YYYY-MM-DD), to'lov turi
let rcFilter = { q: '', month: '', date: '', pay: '' };
const UZ_MONTHS = ['Yanvar', 'Fevral', 'Mart', 'Aprel', 'May', 'Iyun',
  'Iyul', 'Avgust', 'Sentabr', 'Oktabr', 'Noyabr', 'Dekabr'];

function resetRcFilter() {
  rcFilter = { q: '', month: '', date: '', pay: '' };
  const s = $('rcSearch'); if (s) s.value = '';
  const d = $('rcDate'); if (d) d.value = '';
  const c = $('rcSearchClear'); if (c) c.classList.add('hidden');
  document.querySelectorAll('#rcPayFilters .rc-chip').forEach((b) =>
    b.classList.toggle('active', b.dataset.pay === ''));
}
function openReceipts() {
  rcSelKey = null;
  resetRcFilter();
  $('receiptsScreen').classList.remove('hidden');
  loadPrinters();
  setRcTab('sales');
}
function closeReceipts() { $('receiptsScreen').classList.add('hidden'); focusScan(); }
function setRcTab(tab) {
  checksTab = tab;
  rcSelKey = null;
  document.querySelectorAll('.rc-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $('rcFilters').classList.toggle('hidden', tab !== 'sales');   // filtr faqat "Hamma cheklar"da
  $('rcDetail').innerHTML = `<div class="rc-empty-detail">Chekni tanlang</div>`;
  renderRcList();
  if (tab === 'sales') { const s = $('rcSearch'); if (s) setTimeout(() => s.focus(), 30); }
}
function payLabel(p) { return p === 'cash' ? 'Naqd' : p === 'card' ? 'Karta' : p === 'click' ? 'CLICK' : p === 'qarz' ? '🤝 Qarz' : p === 'rasxod' ? '🔻 Rasxod' : p === 'qaytarish' ? '↩️ Qaytarish' : 'Boshqa'; }
// Chek turi yorlig'i — qaytarish (refund) bo'lsa alohida ko'rsatamiz
function isReturnSale(s) { return !!(s && (s.is_return || s.payment === 'qaytarish')); }
function saleKindLabel(s) { return isReturnSale(s) ? '↩️ Qaytarish' : payLabel(s.payment); }

async function renderRcList() {
  const box = $('rcList');
  if (checksTab === 'sales') {
    const r = await window.kassa.getRecentSales();
    rcAllSales = r.sales || [];
    buildRcMonths();
    renderRcSales();
  } else {
    rcDrafts = await window.kassa.getDrafts();
    if (!rcDrafts.length) { box.innerHTML = `<div class="rc-empty">Qoralama yo'q</div>`; return; }
    box.innerHTML = rcDrafts.map((d, i) => {
      const time = (d.created_at || '').slice(11, 16);
      const cnt = (d.items || []).reduce((a, it) => a + it.qty, 0);
      const tot = (d.items || []).reduce((a, it) => a + it.price_sum * it.qty, 0);
      const cl = d.client ? ' · ' + escapeHtml(d.client.name) : '';
      return `<div class="rc-row ${rcSelKey === 'd' + i ? 'active' : ''}" data-i="${i}">
        <div class="rc-row-top"><span class="rc-row-time">${time}</span><span class="rc-row-total">${nf(tot)}</span></div>
        <div class="rc-row-sub"><span class="rc-pay">Qoralama</span><span>${cnt} dona${cl}</span></div>
      </div>`;
    }).join('');
    box.querySelectorAll('.rc-row').forEach((el) => el.addEventListener('click', () => {
      rcSelKey = 'd' + el.dataset.i; renderRcList(); renderDraftDetail(rcDrafts[Number(el.dataset.i)]);
    }));
  }
}

// Oy yorlig'i: 'YYYY-MM' → 'Iyun 2026'
function uzMonthLabel(ym) {
  const m = Number((ym || '').slice(5, 7));
  const y = (ym || '').slice(0, 4);
  return (UZ_MONTHS[m - 1] || ym) + ' ' + y;
}
// Mavjud cheklardagi oylardan tugmalar yasaymiz (eng yangisi birinchi)
function buildRcMonths() {
  const box = $('rcMonths');
  if (!box) return;
  const set = new Set();
  for (const s of rcAllSales) { const ym = (s.created_at || '').slice(0, 7); if (ym) set.add(ym); }
  const months = Array.from(set).sort().reverse();
  if (rcFilter.month && !set.has(rcFilter.month)) rcFilter.month = '';   // tanlangan oy yo'qolsa — tozalaymiz
  box.innerHTML = `<button class="rc-chip${rcFilter.month === '' ? ' active' : ''}" data-month="">Hammasi</button>` +
    months.map((ym) => `<button class="rc-chip${rcFilter.month === ym ? ' active' : ''}" data-month="${ym}">${escapeHtml(uzMonthLabel(ym))}</button>`).join('');
  box.querySelectorAll('.rc-chip').forEach((b) => b.addEventListener('click', () => {
    rcFilter.month = b.dataset.month;
    rcFilter.date = ''; const d = $('rcDate'); if (d) d.value = '';   // oy va sana o'zaro almashadi
    box.querySelectorAll('.rc-chip').forEach((x) => x.classList.toggle('active', x === b));
    renderRcSales();
  }));
}
// To'lov turi filtri: '' = hammasi, qaytarish = refundlar, qarz = nasiya, qolgani — cash/card/click
function rcMatchesPay(s, pay) {
  if (!pay) return true;
  if (pay === 'qaytarish') return isReturnSale(s);
  if (isReturnSale(s)) return false;          // refundlar faqat "Qaytarish" filtrida
  if (pay === 'qarz') return s.payment === 'qarz' || !!s.is_nasiya;
  return s.payment === pay;
}
// Filtrlarni qo'llaymiz: oy + sana + to'lov + matn (chek№/SKU/shtrix/nomi/klient)
function applyRcFilter(list) {
  const q = (rcFilter.q || '').trim().toLowerCase();
  return list.filter((s) => {
    if (rcFilter.month && !String(s.created_at || '').startsWith(rcFilter.month)) return false;
    if (rcFilter.date && !String(s.created_at || '').startsWith(rcFilter.date)) return false;
    if (!rcMatchesPay(s, rcFilter.pay)) return false;
    if (q) {
      const head = (String(s.receipt_no || '') + ' ' + String(s.id || '') + ' ' + String(s.client_name || '')).toLowerCase();
      let ok = head.includes(q);
      if (!ok) ok = (s.items || []).some((it) =>
        String(it.sku || '').toLowerCase().includes(q) ||
        String(it.barcode || '').toLowerCase().includes(q) ||
        String(it.product_id || '').toLowerCase().includes(q) ||
        String(it.name || '').toLowerCase().includes(q));
      if (!ok) return false;
    }
    return true;
  });
}
function updateRcResultMeta(list) {
  const el = $('rcResultMeta');
  if (!el) return;
  if (!rcAllSales.length) { el.textContent = ''; return; }
  const sum = list.reduce((a, s) => a + (Number(s.total_sum) || 0), 0);
  const filtered = rcFilter.q || rcFilter.month || rcFilter.date || rcFilter.pay;
  el.textContent = `${list.length} ta chek · ${nf(sum)} so'm` + (filtered ? ` (jami ${rcAllSales.length} tadan)` : '');
}
// Filtrlangan cheklarni chizamiz (serverdan qayta so'ramaydi — faqat lokal filtr)
function renderRcSales() {
  const box = $('rcList');
  rcSales = applyRcFilter(rcAllSales);
  updateRcResultMeta(rcSales);
  if (!rcSales.length) {
    box.innerHTML = `<div class="rc-empty">${rcAllSales.length ? 'Mos chek topilmadi' : "Sotuvlar yo'q"}</div>`;
    return;
  }
  box.innerHTML = rcSales.map((s, i) => {
    const time = (s.created_at || '').slice(5, 16);   // MM-DD HH:MM
    const cnt = (s.items || []).reduce((a, it) => a + it.qty, 0);
    const cl = s.client_name ? ' · ' + escapeHtml(s.client_name) : '';
    const dev = [s.cashier_name, s.source].filter(Boolean).map(escapeHtml).join(' · ');
    const ret = isReturnSale(s);
    return `<div class="rc-row ${rcSelKey === 's' + i ? 'active' : ''}" data-i="${i}">
      <div class="rc-row-top"><span class="rc-row-time">${s.receipt_no ? ('№' + escapeHtml(s.receipt_no) + ' · ') : ''}${time}</span><span class="rc-row-total"${ret ? ' style="color:var(--danger)"' : ''}>${nf(s.total_sum)}</span></div>
      <div class="rc-row-sub"><span class="rc-pay">${saleKindLabel(s)}</span><span>${cnt} dona${cl}</span></div>
      ${dev ? `<div class="rc-row-dev">${dev}</div>` : ''}
    </div>`;
  }).join('');
  box.querySelectorAll('.rc-row').forEach((el) => el.addEventListener('click', () => {
    rcSelKey = 's' + el.dataset.i; renderRcSales(); renderSaleDetail(rcSales[Number(el.dataset.i)]);
  }));
}

function rcItemRows(items) {
  return (items || []).map((it, n) => {
    const orig = Number(it.orig) || it.price_sum;
    const disc = orig > it.price_sum + 0.5;
    const priceCell = disc
      ? `<span class="rc-it-old">${nf(orig)}</span><span class="rc-it-new">${nf(it.price_sum)}</span>`
      : `<span class="rc-it-new">${nf(it.price_sum)}</span>`;
    return `<tr>
      <td>${n + 1}</td>
      <td><div class="rc-it-name">${escapeHtml(it.name || '')}</div><div class="rc-it-sku">${it.sku || it.product_id || ''}${it.barcode ? (' · ' + it.barcode) : ''}</div></td>
      <td class="r">${it.qty}</td>
      <td class="r">${priceCell}</td>
      <td class="r"><b>${nf(it.price_sum * it.qty)}</b></td>
    </tr>`;
  }).join('');
}
function renderSaleDetail(s) {
  if (!s) return;
  const subtotal = s.subtotal_sum || (s.items || []).reduce((a, it) => a + it.price_sum * it.qty, 0);
  const total = s.total_sum;
  const disc = (s.discount_sum != null) ? s.discount_sum : Math.max(0, subtotal - total);
  const pct = subtotal > 0 ? Math.round(disc / subtotal * 100) : 0;
  const ret = isReturnSale(s);
  const title = ret ? ('Qaytarish ' + (s.receipt_no || ('№' + (s.id || ''))))
    : (s.receipt_no ? ('Chek ' + s.receipt_no) : (s.id ? ('Chek №' + s.id) : 'Chek'));
  $('rcDetail').innerHTML = `
    <div class="rc-d-head"><span class="rc-d-title${ret ? ' is-return' : ''}">${title}</span></div>
    <div class="rc-d-meta">
      <span>Vaqt: <b>${escapeHtml(s.created_at || '')}</b></span>
      ${s.cashier_name ? `<span>Kassir: <b>${escapeHtml(s.cashier_name)}</b></span>` : ''}
      ${s.source ? `<span>Qurilma: <b>${escapeHtml(s.source)}</b></span>` : ''}
      <span>To'lov: <b>${saleKindLabel(s)}</b></span>
      ${s.client_name ? `<span>Klient: <b>${escapeHtml(s.client_name)}</b></span>` : ''}
    </div>
    <table class="rc-items">
      <thead><tr><th>№</th><th>Nomi</th><th class="r">Miqdor</th><th class="r">Narx</th><th class="r">Summa</th></tr></thead>
      <tbody>${rcItemRows(s.items)}</tbody>
    </table>
    <div class="rc-totals">
      <div class="rc-tot-row"><span>Oraliq jami</span><span>${nf(subtotal)} so'm</span></div>
      ${disc > 0 ? `<div class="rc-tot-row disc"><span>Chegirma (${pct}%)</span><span>−${nf(disc)} so'm</span></div>` : ''}
      <div class="rc-tot-row grand"><span>JAMI</span><span>${nf(total)} so'm</span></div>
    </div>
    ${s.qrLink ? `<div class="rc-tot-row grand" style="margin-top:12px;border-top:1px solid var(--line);padding-top:10px"><span>QR to'lov</span></div>
    <div class="c" style="margin:8px 0"><img src="https://api.qrserver.com/v1/create-qr-code/?size=120x120&data=${encodeURIComponent(s.qrLink)}" style="width:120px;height:120px;display:block;margin:0 auto"><div style="font-size:11px;color:var(--muted);word-break:break-all;margin-top:4px">${escapeHtml(s.qrLink)}</div></div>` : ''}
    <button class="rc-print" id="rcPrintBtn"><svg class="ico" viewBox="0 0 24 24"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>Chop etish</button>
    ${canReturn(s) ? `<button class="rc-return" id="rcReturnBtn"><svg class="ico" viewBox="0 0 24 24"><polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>Qaytarish</button>` : ''}`;
  $('rcPrintBtn').addEventListener('click', () => printReceipt(s));
  const rb = $('rcReturnBtn');
  if (rb) rb.addEventListener('click', () => openReturnModal(s));
}

const SHOP_PHONE = '+998 77877 55 33';
const SHOP_ADDR = 'Sergeli chinor bekati';
function buildReceiptHtml(s) {
  const subtotal = s.subtotal_sum || (s.items || []).reduce((a, it) => a + it.price_sum * it.qty, 0);
  const total = s.total_sum;
  const disc = (s.discount_sum != null) ? s.discount_sum : Math.max(0, subtotal - total);
  const date = (s.created_at || '').slice(0, 10).split('-').reverse().join('.');
  const time = (s.created_at || '').slice(11, 16);
  const kassaNo = s.receipt_no ? String(s.receipt_no).split('-')[0] : ((window._lastState && window._lastState.kassaNo) || 1);
  const rows = (s.items || []).map((it) => {
    const line = `${it.qty} × ${nf(it.price_sum)} = ${nf(it.price_sum * it.qty)}`;
    const bc = String(it.barcode || '').trim();
    const sk = it.sku || it.product_id || '';
    const skuLine = (bc || sk) ? `<div class="sub">${escapeHtml(bc)}${bc && sk ? ' · ' : ''}${sk}</div>` : '';
    return `<div class="it"><div class="it-name">${escapeHtml(it.name || '')}</div><div class="it-line">${line}</div>${skuLine}</div>`;
  }).join('');
  // QR — sotuvda ham, eski chekni qayta chop etishda ham bir xil chiqadi
  const qrUrl = s.qrLink
    ? `https://api.qrserver.com/v1/create-qr-code/?size=320x320&qzone=1&data=${encodeURIComponent(s.qrLink)}`
    : '';
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    @page{margin:0}
    *{margin:0;padding:0;box-sizing:border-box}
    html{width:80mm}
    /* XP-80T: qog'oz 80mm, bosiladigan maydon 72mm — kontentni shu maydonga markazlaymiz */
    body{width:72mm;margin:0 auto;padding:4px 2px;color:#000;background:#fff;
      font-family:Arial,'Helvetica Neue',Helvetica,sans-serif;font-size:18px;line-height:1.32;font-weight:700;
      -webkit-print-color-adjust:exact;print-color-adjust:exact;color-adjust:exact}
    .c{text-align:center}.r{text-align:right}
    .logo{display:block;margin:0 auto 3px;width:42mm;max-width:100%;height:auto}
    .phone{text-align:center;font-size:19px;font-weight:800}
    .addr{text-align:center;font-size:15px;font-weight:600}
    hr{border:0;border-top:2px solid #000;margin:5px 0}
    .dash{border:0;border-top:2px dashed #000;margin:5px 0}
    .hrow{display:flex;justify-content:space-between;gap:10px;font-size:16px;margin:1px 0}
    .it{margin:4px 0}
    .it-name{font-weight:800;font-size:18px}
    .it-line{text-align:right;font-size:17px}
    .sub{font-size:13px;font-weight:600}
    .tot{display:flex;justify-content:space-between;font-size:17px;margin:1px 0}
    .jami{display:flex;justify-content:space-between;font-size:27px;font-weight:900;margin-top:5px}
    .pay{display:flex;justify-content:space-between;font-size:18px;margin-top:1px}
    .qrttl{text-align:center;font-weight:800;font-size:17px;margin-bottom:3px}
    .qrimg{display:block;margin:0 auto;width:40mm;height:40mm}
    .qrcap{text-align:center;font-size:12px;font-weight:600;word-break:break-all;margin-top:2px}
    .thanks{text-align:center;margin-top:8px;font-size:20px;font-weight:900}
  </style></head><body>
    ${LOGO ? `<img class="logo" src="${LOGO}">` : '<div class="c" style="font-size:34px;font-weight:900;letter-spacing:2px">CHINOR</div>'}
    <div class="phone">${SHOP_PHONE}</div>
    <div class="addr">${SHOP_ADDR}</div>
    <hr>
    <div class="hrow"><span>Chek №: ${s.receipt_no || s.id || ''}</span><span>${date}</span></div>
    <div class="hrow"><span>Kassa-${kassaNo}</span><span>${time}</span></div>
    ${s.cashier_name ? `<div class="hrow"><span>Sotuvchi:</span><span>${escapeHtml(s.cashier_name)}</span></div>` : ''}
    ${s.client_name ? `<div class="hrow"><span>Klient:</span><span>${escapeHtml(s.client_name)}</span></div>` : ''}
    <hr class="dash">
    ${rows}
    <hr class="dash">
    <div class="tot"><span>Oraliq jami</span><span>${nf(subtotal)}</span></div>
    ${disc > 0 ? `<div class="tot"><span>Chegirma</span><span>-${nf(disc)}</span></div>` : ''}
    <div class="jami"><span>JAMI</span><span>${nf(total)}</span></div>
    <div class="pay"><span>To'lov</span><span>${payLabel(s.payment)}</span></div>
    ${qrUrl ? `<hr class="dash"><div class="qrttl">To'lov uchun · QR</div><img class="qrimg" src="${qrUrl}"><div class="qrcap">${escapeHtml(s.qrLink)}</div>` : ''}
    <hr class="dash">
    <div class="thanks">Haridingiz uchun rahmat!</div>
  </body></html>`;
}
async function printReceipt(s) {
  if (!s) return;
  if (!LOGO) { try { LOGO = await window.kassa.getLogo(); } catch (_) {} }
  const res = await window.kassa.printReceipt(buildReceiptHtml(s));
  if (res && res.ok) toast('Chek chop etildi');
  else if (res && res.reason && res.reason !== 'cancelled') toast('Chop etib bo\'lmadi (printerni tekshiring)', true);
}
async function loadPrinters() {
  const sel = $('rcPrinter');
  if (!sel) return;
  const { printers, current } = await window.kassa.getPrinters();
  if (!printers || !printers.length) { sel.innerHTML = '<option value="">Printer topilmadi</option>'; return; }
  sel.innerHTML = '<option value="">Standart printer</option>' +
    printers.map((p) => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.display)}${p.isDefault ? ' (standart)' : ''}</option>`).join('');
  sel.value = current || '';
}
function renderDraftDetail(d) {
  if (!d) return;
  const subtotal = (d.items || []).reduce((a, it) => a + it.price_sum * it.qty, 0);
  $('rcDetail').innerHTML = `
    <div class="rc-d-head"><span class="rc-d-title">Qoralama</span></div>
    <div class="rc-d-meta">
      <span>Vaqt: <b>${escapeHtml(d.created_at || '')}</b></span>
      ${d.client ? `<span>Klient: <b>${escapeHtml(d.client.name)}</b></span>` : ''}
    </div>
    <table class="rc-items">
      <thead><tr><th>№</th><th>Nomi</th><th class="r">Miqdor</th><th class="r">Narx</th><th class="r">Summa</th></tr></thead>
      <tbody>${rcItemRows(d.items)}</tbody>
    </table>
    <div class="rc-totals">
      <div class="rc-tot-row grand"><span>JAMI</span><span>${nf(subtotal)} so'm</span></div>
    </div>
    <button class="rc-resume" id="rcResumeBtn">Davom etish (savatga yuklash)</button>`;
  $('rcResumeBtn').addEventListener('click', () => resumeDraft(d.id));
}
async function parkDraft() {
  if (!CART.length) { toast('Savat bo\'sh', true); return; }
  await window.kassa.saveDraft({ items: CART, client: selClient });
  CART = []; selIndex = -1; selClient = null; clearOverride(); updateClientChip(); resetKp(); renderCart();
  toast('📝 Qoralamaga saqlandi'); focusScan();
}
async function resumeDraft(id) {
  const drafts = await window.kassa.getDrafts();
  const d = drafts.find((x) => x.id === id);
  if (!d) return;
  CART = d.items.map((it) => ({ ...it }));
  selClient = d.client || null;
  selIndex = CART.length ? 0 : -1;
  clearOverride(); updateClientChip(); renderCart();
  await window.kassa.removeDraft(id);
  closeReceipts(); toast('Qoralama ochildi'); focusScan();
}

// ── Qaytarish (refund) ─────────────────────────────────────────────────
let returnSale = null;       // qaytarilayotgan asl chek
let returnQty = [];          // har bir item uchun qaytariladigan (savatdagi) miqdor
let returnRemain = [];       // har bir item uchun QOLGAN (qaytarish mumkin) miqdor
let returnMethod = 'cash';   // pul qaytarish usuli: cash|card|click|debt
let rpEditIndex = -1;        // chap panelda hozir miqdor kiritilayotgan qator (-1 = yo'q)

function returnMethodLabel(m) {
  return m === 'card' ? 'Kartaga' : m === 'click' ? 'Click' :
    m === 'debt' ? 'Qarzdan ayirildi' : 'Naqd';
}
function setReturnMethod(m) {
  returnMethod = m;
  document.querySelectorAll('.ret-m').forEach((b) =>
    b.classList.toggle('active', b.dataset.m === m));
}

// Shu asl chek bo'yicha har bir mahsulotdan AVVAL qaytarilgan umumiy miqdor.
// rcSales ichidagi (shu chekka bog'langan) qaytarish yozuvlaridan yig'iladi —
// shunda bir narsani ikki marta qaytarib bo'lmaydi.
function returnedSoFar(sale) {
  const map = {};
  const key = sale && sale.receipt_no;
  if (!key) return map;   // chek raqami yo'q — bog'lab bo'lmaydi
  for (const r of rcSales) {
    if (!isReturnSale(r)) continue;
    if (String(r.orig_receipt_no || '') !== String(key)) continue;
    for (const it of (r.items || [])) {
      map[it.product_id] = (map[it.product_id] || 0) + Math.abs(Number(it.qty) || 0);
    }
  }
  return map;
}
// Shu chekda kamida bitta mahsulot hali to'liq qaytarilmaganmi (qolgan > 0)?
function hasReturnable(s) {
  const done = returnedSoFar(s);
  return (s.items || []).some((it) =>
    (Math.max(0, Number(it.qty) || 0) - (done[it.product_id] || 0)) > 0);
}
// Qaytarib bo'ladimi? Ichki rasxod / o'zi qaytarish / to'liq qaytarilgan — yo'q.
// Qarz (nasiya) sotuvni ham qaytarsa bo'ladi — pul "qarzdan ayirish" bilan qaytadi.
function canReturn(s) {
  if (!s || isReturnSale(s)) return false;
  if (s.is_internal || s.payment === 'rasxod') return false;
  if (!(s.items || []).length) return false;
  return hasReturnable(s);
}
function returnMaxQty(i) { return Math.max(0, Number(returnRemain[i]) || 0); }
function returnTotalSum() {
  return (returnSale.items || []).reduce((a, it, i) => a + (Number(it.price_sum) || 0) * returnQty[i], 0);
}
function updateReturnTotal() { $('returnTotal').textContent = nf(returnTotalSum()); }
// Shu qator uchun hali savatga qo'shsa bo'ladigan miqdor = qolgan − savatdagi
function retAvail(i) {
  return Math.max(0, (Number(returnRemain[i]) || 0) - (Number(returnQty[i]) || 0));
}
// CHAP panel — chekdagi tovarlar. Ustiga bosilsa miqdor maydoni ochiladi.
function renderReturnSource() {
  const box = $('retSrcList');
  const items = returnSale.items || [];
  const html = items.map((it, i) => {
    const sold = Math.max(0, Number(it.qty) || 0);
    const remain = Math.max(0, Number(returnRemain[i]) || 0);
    const avail = retAvail(i);
    if (remain <= 0) {   // bu sessiyadan oldin to'liq qaytarilgan
      return `<div class="ret-src-row done"><div class="rs-info"><div class="rs-name">${escapeHtml(it.name || '')}</div><div class="rs-sub">to'liq qaytarilgan</div></div><span class="rs-avail">0</span></div>`;
    }
    if (avail <= 0) {    // hammasi savatga o'tgan
      return `<div class="ret-src-row done"><div class="rs-info"><div class="rs-name">${escapeHtml(it.name || '')}</div><div class="rs-sub">hammasi qaytarishda</div></div><span class="rs-avail">0</span></div>`;
    }
    if (rpEditIndex === i) {
      return `<div class="ret-src-row editing" data-i="${i}">
        <div class="rs-info"><div class="rs-name">${escapeHtml(it.name || '')}</div><div class="rs-sub">${nf(it.price_sum)} so'm · qoldi ${avail}</div></div>
        <div class="rs-edit">
          <input class="rs-qty" type="number" min="1" max="${avail}" step="1" value="${avail}">
          <button class="rs-ok" data-act="ok" title="Qo'shish (Enter)">✓</button>
        </div>
      </div>`;
    }
    return `<div class="ret-src-row" data-i="${i}">
      <div class="rs-info"><div class="rs-name">${escapeHtml(it.name || '')}</div><div class="rs-sub">${nf(it.price_sum)} so'm · sotilgan ${sold}</div></div>
      <span class="rs-avail">${avail}</span>
    </div>`;
  }).join('');
  box.innerHTML = html || `<div class="ret-empty">Qaytariladigan tovar yo'q</div>`;
  box.querySelectorAll('.ret-src-row[data-i]').forEach((row) => {
    const i = Number(row.dataset.i);
    if (rpEditIndex === i) {
      const input = row.querySelector('.rs-qty');
      const ok = row.querySelector('[data-act="ok"]');
      if (input) {
        setTimeout(() => { input.focus(); input.select(); }, 0);
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); commitRetAdd(i, input.value); }
          else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); rpEditIndex = -1; renderReturnPicker(); }
        });
      }
      if (ok) ok.addEventListener('click', () => commitRetAdd(i, input ? input.value : ''));
    } else {
      row.addEventListener('click', () => { rpEditIndex = i; renderReturnPicker(); });
    }
  });
}
// O'NG panel — qaytarish savati.
function renderReturnBasket() {
  const box = $('retBasketList');
  const items = returnSale.items || [];
  const html = items.map((it, i) => {
    const q = Number(returnQty[i]) || 0;
    if (q <= 0) return '';
    const sum = (Number(it.price_sum) || 0) * q;
    return `<div class="ret-bk-row" data-i="${i}">
      <div class="bk-info"><div class="bk-name">${escapeHtml(it.name || '')}</div><div class="bk-sub">${q} × ${nf(it.price_sum)} = <span class="bk-sum">${nf(sum)} so'm</span></div></div>
      <button class="bk-del" data-act="del" title="Olib tashlash">✕</button>
    </div>`;
  }).join('');
  box.innerHTML = html || `<div class="ret-empty">Hozircha tanlanmadi.<br>Chap tomondan tovar tanlang.</div>`;
  box.querySelectorAll('.ret-bk-row').forEach((row) => {
    const i = Number(row.dataset.i);
    const del = row.querySelector('[data-act="del"]');
    if (del) del.addEventListener('click', () => { returnQty[i] = 0; rpEditIndex = -1; renderReturnPicker(); });
  });
}
function renderReturnPicker() {
  renderReturnSource();
  renderReturnBasket();
  updateReturnTotal();
}
// Chap paneldagi qatorni savatga qo'shish. val bo'sh/xato → hammasini (qolganini).
function commitRetAdd(i, val) {
  const avail = retAvail(i);
  if (avail <= 0) { rpEditIndex = -1; renderReturnPicker(); return; }
  let v = parseInt(val, 10);
  if (!Number.isFinite(v) || v <= 0) v = avail;   // Enter / bo'sh → hammasi
  v = Math.min(avail, Math.max(1, v));            // sotilgandan ortiq emas
  returnQty[i] = (Number(returnQty[i]) || 0) + v;
  rpEditIndex = -1;
  renderReturnPicker();
}
// "Hammasini →" — chekdagi qolgan barcha tovarni savatga.
function returnAddAll() {
  (returnSale.items || []).forEach((it, i) => { returnQty[i] = Math.max(0, Number(returnRemain[i]) || 0); });
  rpEditIndex = -1;
  renderReturnPicker();
}
// "Tozalash" — savatni bo'shatadi.
function returnClearBasket() {
  returnQty = (returnSale.items || []).map(() => 0);
  rpEditIndex = -1;
  renderReturnPicker();
}
function openReturnModal(s) {
  if (!canReturn(s)) { toast('Bu chek allaqachon to\'liq qaytarilgan', true); return; }
  returnSale = s;
  const done = returnedSoFar(s);
  // har bir item uchun qolgan = sotilgan − avval qaytarilgan
  returnRemain = (s.items || []).map((it) =>
    Math.max(0, (Math.max(0, Number(it.qty) || 0)) - (done[it.product_id] || 0)));
  // Savat bo'sh boshlanadi — kassir chap paneldan kerakli tovarni terib o'tkazadi
  // (20 tovarli chekda 19 tasini 0 ga tushirish shart emas).
  returnQty = (s.items || []).map(() => 0);
  // Agar qaytariladigan bitta qator bo'lsa — darrov miqdor maydonini ochamiz (Enter → hammasi).
  const liveIdx = returnRemain.map((r, i) => (r > 0 ? i : -1)).filter((i) => i >= 0);
  rpEditIndex = (liveIdx.length === 1) ? liveIdx[0] : -1;
  // Pul qaytarish usuli: nasiya sotuv → qarzdan ayirish; aks holda asl to'lov usuli.
  // «Qarzdan ayirish» faqat mijozli sotuvlarda ko'rinadi.
  const hasClient = Number(s.client_id) > 0;
  const isNasiya = !!(s.is_nasiya || s.payment === 'qarz');
  const debtBtn = document.querySelector('.ret-m-debt');
  if (debtBtn) debtBtn.classList.toggle('hidden', !hasClient);
  const def = isNasiya ? 'debt' : ((s.payment === 'card' || s.payment === 'click') ? s.payment : 'cash');
  setReturnMethod(hasClient ? def : (def === 'debt' ? 'cash' : def));
  $('returnTitle').textContent = '↩️ Qaytarish · ' + (s.receipt_no ? ('№' + s.receipt_no) : ('№' + (s.id || '')));
  renderReturnPicker();
  openModal('returnModal');
}
async function confirmReturn() {
  if (!returnSale) return;
  const items = (returnSale.items || [])
    .map((it, i) => ({ product_id: it.product_id, name: it.name, sku: it.sku, barcode: it.barcode,
                       qty: returnQty[i], price_sum: it.price_sum, orig: it.orig }))
    .filter((it) => it.qty > 0);
  if (!items.length) { toast('Qaytariladigan miqdorni tanlang', true); return; }
  const refundSum = items.reduce((a, it) => a + it.price_sum * it.qty, 0);
  // «Qarzdan ayirish» faqat mijoz bo'lsa; bo'lmasa naqdga tushiramiz
  const clientId = Number(returnSale.client_id) || 0;
  const method = (returnMethod === 'debt' && !clientId) ? 'cash' : returnMethod;
  const res = await window.kassa.recordReturn({
    items, refundSum, method, clientId,
    origReceiptNo: returnSale.receipt_no || '',
    origSaleId: returnSale.id || '',
    clientName: returnSale.client_name || '',
  });
  if (!res || !res.ok) { toast('❌ Xato: ' + ((res && res.error) || 'saqlanmadi'), true); return; }
  closeModal('returnModal');
  // Qaytarish cheki (alohida tartibda)
  const ret = {
    items, method,
    total_sum: refundSum, subtotal_sum: refundSum,
    cashier_name: (window._lastState && window._lastState.cashier && window._lastState.cashier.name) || '',
    client_name: returnSale.client_name || '',
    created_at: new Date().toISOString(),
    receipt_no: (res.ret && res.ret.receipt_no) || '',
    orig_receipt_no: returnSale.receipt_no || '',
  };
  if (!LOGO) { try { LOGO = await window.kassa.getLogo(); } catch (_) {} }
  await window.kassa.printReceipt(buildReturnReceiptHtml(ret));
  if (res.state) applyState(res.state);
  await loadCatalog();
  returnSale = null;
  await renderRcList();
  $('rcDetail').innerHTML = `<div class="rc-empty-detail">Chekni tanlang</div>`;
  rcSelKey = null;
  toast(method === 'debt'
    ? `↩️ Qarzdan ayirildi · ${nf(refundSum)} so'm`
    : `↩️ Qaytarildi (${returnMethodLabel(method)}) · ${nf(refundSum)} so'm`);
}
// Qaytarish cheki — sotuv chekiga o'xshash, lekin "QAYTARISH" deb belgilanadi
function buildReturnReceiptHtml(s) {
  const total = s.total_sum;
  const date = (s.created_at || '').slice(0, 10).split('-').reverse().join('.');
  const time = (s.created_at || '').slice(11, 16);
  const kassaNo = s.receipt_no ? String(s.receipt_no).split('-')[0] : ((window._lastState && window._lastState.kassaNo) || 1);
  const rows = (s.items || []).map((it) => {
    const line = `${it.qty} × ${nf(it.price_sum)} = ${nf(it.price_sum * it.qty)}`;
    const bc = String(it.barcode || '').trim();
    const sk = it.sku || it.product_id || '';
    const skuLine = (bc || sk) ? `<div class="sub">${escapeHtml(bc)}${bc && sk ? ' · ' : ''}${sk}</div>` : '';
    return `<div class="it"><div class="it-name">${escapeHtml(it.name || '')}</div><div class="it-line">${line}</div>${skuLine}</div>`;
  }).join('');
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    @page{margin:0}
    *{margin:0;padding:0;box-sizing:border-box}
    html{width:80mm}
    body{width:72mm;margin:0 auto;padding:4px 2px;color:#000;background:#fff;
      font-family:Arial,'Helvetica Neue',Helvetica,sans-serif;font-size:18px;line-height:1.32;font-weight:700;
      -webkit-print-color-adjust:exact;print-color-adjust:exact;color-adjust:exact}
    .c{text-align:center}
    .logo{display:block;margin:0 auto 3px;width:42mm;max-width:100%;height:auto}
    .phone{text-align:center;font-size:19px;font-weight:800}
    .addr{text-align:center;font-size:15px;font-weight:600}
    .badge{text-align:center;font-size:22px;font-weight:900;margin:4px 0;border:2px solid #000;padding:3px 0}
    hr{border:0;border-top:2px solid #000;margin:5px 0}
    .dash{border:0;border-top:2px dashed #000;margin:5px 0}
    .hrow{display:flex;justify-content:space-between;gap:10px;font-size:16px;margin:1px 0}
    .it{margin:4px 0}
    .it-name{font-weight:800;font-size:18px}
    .it-line{text-align:right;font-size:17px}
    .sub{font-size:13px;font-weight:600}
    .jami{display:flex;justify-content:space-between;font-size:27px;font-weight:900;margin-top:5px}
    .pay{display:flex;justify-content:space-between;font-size:18px;margin-top:1px}
    .thanks{text-align:center;margin-top:8px;font-size:18px;font-weight:800}
  </style></head><body>
    ${LOGO ? `<img class="logo" src="${LOGO}">` : '<div class="c" style="font-size:34px;font-weight:900;letter-spacing:2px">CHINOR</div>'}
    <div class="phone">${SHOP_PHONE}</div>
    <div class="addr">${SHOP_ADDR}</div>
    <div class="badge">↩ QAYTARISH</div>
    <div class="hrow"><span>Qaytarish №: ${s.receipt_no || ''}</span><span>${date}</span></div>
    <div class="hrow"><span>Kassa-${kassaNo}</span><span>${time}</span></div>
    ${s.orig_receipt_no ? `<div class="hrow"><span>Asl chek:</span><span>№${escapeHtml(String(s.orig_receipt_no))}</span></div>` : ''}
    ${s.cashier_name ? `<div class="hrow"><span>Sotuvchi:</span><span>${escapeHtml(s.cashier_name)}</span></div>` : ''}
    ${s.client_name ? `<div class="hrow"><span>Klient:</span><span>${escapeHtml(s.client_name)}</span></div>` : ''}
    <hr class="dash">
    ${rows}
    <hr class="dash">
    <div class="jami"><span>QAYTARILDI</span><span>${nf(total)}</span></div>
    <div class="pay"><span>Usul</span><span>${returnMethodLabel(s.method)}</span></div>
    <hr class="dash">
    <div class="thanks">Noqulaylik uchun uzr so'raymiz</div>
  </body></html>`;
}

// ── Smena (shift) ───────────────────────────────────────────────────────
function tashNow() {
  const d = new Date(Date.now() + 5 * 3600 * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
         `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}
async function openShiftModal() {
  const sh = await window.kassa.getShift();
  $('shiftTitle').textContent = sh ? '🟢 Smena ochiq' : 'Smena yopiq';
  if (sh) renderShiftOpen(sh, await window.kassa.shiftSummary());
  else renderShiftClosed();
  openModal('shiftModal');
}
function renderShiftClosed() {
  $('shiftBody').innerHTML = `
    <div class="shift-note">Smena yopiq. Yangi smena oching — undan keyingi barcha
      sotuv va qaytarishlar shu smenaga yoziladi va yopishda Z-hisobot chiqadi.</div>
    <div class="edit-field"><label>Boshlang'ich naqd — kassadagi mavjud pul (so'm)</label>
      <input id="shiftOpenCash" type="number" min="0" step="1000" value="0"></div>
    <div class="edit-actions">
      <button class="modal-btn" data-close>Bekor</button>
      <button id="shiftOpenBtn" class="modal-btn primary">Smena ochish</button>
    </div>`;
  $('shiftOpenBtn').addEventListener('click', doOpenShift);
}
function zRow(label, val, cls = '') {
  return `<div class="z-row${cls}"><span>${label}</span><span>${nf(val)}</span></div>`;
}
function renderShiftOpen(sh, z) {
  z = z || {};
  const s = z.sales || {}, r = z.returns || {}, inr = z.internal || {};
  $('shiftBody').innerHTML = `
    <div class="z-meta">
      <span>Smena: <b>${escapeHtml(sh.no || '')}</b></span>
      <span>Ochilgan: <b>${escapeHtml(sh.openedAt || '')}</b></span>
      ${z.openedBy ? `<span>Kassir: <b>${escapeHtml(z.openedBy.name || '')}</b></span>` : ''}
    </div>
    <div class="z-sec">
      <div class="z-h">Sotuvlar · ${s.count || 0} ta</div>
      ${zRow('Naqd', s.cash || 0)}${zRow('Karta', s.card || 0)}
      ${zRow('Click', s.click || 0)}${zRow('Qarz (nasiya)', s.nasiya || 0)}
      ${zRow('Jami sotuv', s.total || 0, ' z-tot')}
    </div>
    <div class="z-sec">
      <div class="z-h">Qaytarishlar · ${r.count || 0} ta</div>
      ${zRow('Naqd', r.cash || 0)}${zRow('Karta', r.card || 0)}
      ${zRow('Click', r.click || 0)}${zRow('Qarzdan ayirildi', r.debt || 0)}
      ${zRow('Jami qaytarish', r.total || 0, ' z-tot')}
    </div>
    <div class="z-sec z-cash">
      <div class="z-h">Naqd kassa</div>
      ${zRow("Boshlang'ich naqd", z.openCash || 0)}
      ${zRow('+ Naqd sotuv', s.cash || 0)}
      ${zRow('− Naqd qaytarish', r.cash || 0)}
      ${z.cashIn ? zRow('+ Naqd kirim', z.cashIn) : ''}
      ${z.cashOut ? zRow('− Naqd chiqim', z.cashOut) : ''}
      ${zRow('= Kutilayotgan naqd', z.expectedCash || 0, ' z-tot')}
    </div>
    ${inr.count ? `<div class="z-sec">${zRow('Chinor ichki rasxod (' + inr.count + ')', inr.total || 0)}</div>` : ''}
    <div class="z-cashmove">
      <input id="cashMoveAmt" type="number" min="0" step="1000" placeholder="Summa (so'm)">
      <button id="cashInBtn" class="modal-btn">＋ Kirim</button>
      <button id="cashOutBtn" class="modal-btn">－ Chiqim</button>
    </div>
    <div class="edit-actions">
      <button id="shiftXBtn" class="modal-btn">🖨 X-hisobot</button>
      <button id="shiftCloseBtn" class="modal-btn danger">Smenani yopish (Z)</button>
    </div>`;
  $('cashInBtn').addEventListener('click', () => doCashMove('in'));
  $('cashOutBtn').addEventListener('click', () => doCashMove('out'));
  $('shiftXBtn').addEventListener('click', () => printShiftReport(z, 'X'));
  $('shiftCloseBtn').addEventListener('click', doCloseShift);
}
async function doOpenShift() {
  const v = Number($('shiftOpenCash').value) || 0;
  const res = await window.kassa.openShift({ openCash: v });
  if (res && res.ok) { if (res.state) applyState(res.state); await openShiftModal(); toast('🟢 Smena ochildi'); }
}
async function doCashMove(type) {
  const amt = Number($('cashMoveAmt').value) || 0;
  if (amt <= 0) { toast('Summani kiriting', true); return; }
  const res = await window.kassa.addCashMove({ type, amount: amt, note: '' });
  if (res && res.ok) {
    if (res.state) applyState(res.state);
    renderShiftOpen(await window.kassa.getShift(), res.summary);
    toast(type === 'out' ? '－ Naqd chiqim yozildi' : '＋ Naqd kirim yozildi');
  }
}
async function doCloseShift() {
  if (!confirm('Smenani yopib, Z-hisobotni chop etamizmi?')) return;
  const res = await window.kassa.closeShift();
  if (res && res.ok && res.summary) {
    if (!LOGO) { try { LOGO = await window.kassa.getLogo(); } catch (_) {} }
    await window.kassa.printReceipt(buildShiftReportHtml(res.summary, 'Z'));
    if (res.state) applyState(res.state);
    closeModal('shiftModal');
    toast('🧾 Z-hisobot chop etildi · Smena yopildi');
  } else { toast('Smena topilmadi', true); }
}
async function printShiftReport(z, kind) {
  if (!LOGO) { try { LOGO = await window.kassa.getLogo(); } catch (_) {} }
  await window.kassa.printReceipt(buildShiftReportHtml(z, kind));
  toast((kind === 'Z' ? 'Z' : 'X') + '-hisobot chop etildi');
}
// X/Z smena hisoboti — termal chek ko'rinishida
function buildShiftReportHtml(z, kind) {
  z = z || {};
  const s = z.sales || {}, r = z.returns || {}, inr = z.internal || {};
  const closed = z.closedAt || tashNow();
  const row = (l, v) => `<div class="zr"><span>${escapeHtml(l)}</span><span>${nf(v)}</span></div>`;
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    @page{margin:0}*{margin:0;padding:0;box-sizing:border-box}html{width:80mm}
    body{width:72mm;margin:0 auto;padding:4px 2px;color:#000;background:#fff;
      font-family:Arial,Helvetica,sans-serif;font-size:17px;line-height:1.3;font-weight:700;
      -webkit-print-color-adjust:exact;print-color-adjust:exact}
    .c{text-align:center}.logo{display:block;margin:0 auto 3px;width:40mm;height:auto}
    .ttl{text-align:center;font-size:23px;font-weight:900;margin:3px 0}
    .phone{text-align:center;font-size:16px;font-weight:800}
    hr{border:0;border-top:2px solid #000;margin:5px 0}
    .dash{border:0;border-top:2px dashed #000;margin:5px 0}
    .meta{font-size:14px;margin:1px 0;display:flex;justify-content:space-between}
    .sh{font-size:16px;font-weight:900;margin:6px 0 2px;text-decoration:underline}
    .zr{display:flex;justify-content:space-between;font-size:16px;margin:1px 0}
    .zr.tot{font-size:19px;font-weight:900;border-top:1.5px solid #000;margin-top:3px;padding-top:2px}
    .big{display:flex;justify-content:space-between;font-size:24px;font-weight:900;margin-top:6px}
  </style></head><body>
    ${LOGO ? `<img class="logo" src="${LOGO}">` : '<div class="c" style="font-size:30px;font-weight:900">CHINOR</div>'}
    <div class="phone">${SHOP_PHONE}</div>
    <div class="ttl">${kind === 'Z' ? 'SMENA YOPILDI (Z)' : 'SMENA HISOBOTI (X)'}</div>
    <hr>
    <div class="meta"><span>Smena №:</span><span>${escapeHtml(z.no || '')}</span></div>
    <div class="meta"><span>Kassa:</span><span>Kassa-${z.kassaNo || 1}</span></div>
    ${z.openedBy ? `<div class="meta"><span>Kassir:</span><span>${escapeHtml(z.openedBy.name || '')}</span></div>` : ''}
    <div class="meta"><span>Ochilgan:</span><span>${escapeHtml(z.openedAt || '')}</span></div>
    <div class="meta"><span>${kind === 'Z' ? 'Yopilgan:' : 'Hozir:'}</span><span>${escapeHtml(closed)}</span></div>
    <hr class="dash">
    <div class="sh">SOTUVLAR · ${s.count || 0} ta</div>
    ${row('Naqd', s.cash || 0)}${row('Karta', s.card || 0)}${row('Click', s.click || 0)}${row('Qarz (nasiya)', s.nasiya || 0)}
    <div class="zr tot"><span>Jami sotuv</span><span>${nf(s.total || 0)}</span></div>
    <div class="sh">QAYTARISHLAR · ${r.count || 0} ta</div>
    ${row('Naqd', r.cash || 0)}${row('Karta', r.card || 0)}${row('Click', r.click || 0)}${row('Qarzdan ayirildi', r.debt || 0)}
    <div class="zr tot"><span>Jami qaytarish</span><span>${nf(r.total || 0)}</span></div>
    ${inr.count ? `<div class="sh">ICHKI RASXOD · ${inr.count} ta</div>${row('Chinor (tannarx)', inr.total || 0)}` : ''}
    <div class="sh">NAQD KASSA</div>
    ${row("Boshlang'ich naqd", z.openCash || 0)}
    ${row('+ Naqd sotuv', s.cash || 0)}
    ${row('− Naqd qaytarish', r.cash || 0)}
    ${z.cashIn ? row('+ Naqd kirim', z.cashIn) : ''}
    ${z.cashOut ? row('− Naqd chiqim', z.cashOut) : ''}
    <div class="big"><span>KASSADA</span><span>${nf(z.expectedCash || 0)}</span></div>
    <hr class="dash">
    <div class="c" style="font-size:13px;font-weight:600">${kind === 'Z' ? 'Smena yopildi' : 'Oraliq hisobot (smena ochiq)'}</div>
  </body></html>`;
}

// Naqd sotish (F6) — to'lov turini Naqdga o'tkazib, oddiy chek bilan sotadi
function cashCheckout() {
  if (!CART.length) { toast('Savat bo\'sh', true); return; }
  payMethod = 'cash';
  document.querySelectorAll('.pay-toggle').forEach((x) => x.classList.toggle('active', x.dataset.pay === 'cash'));
  sell();
}

// ── Mahsulot ro'yxati (past) ───────────────────────────────────────────
async function loadCatalog() {
  CATALOG = await window.kassa.getCatalog();
  CATALOG.sort((a, b) => (a.id || 0) - (b.id || 0));  // SKU bo'yicha tartib
  renderList($('scanInput').value);
}
function renderList(filter = '') {
  const box = $('productList');
  const list = matchProducts(filter).slice(0, 300);
  if (!list.length) { box.innerHTML = `<div class="list-empty">Mahsulot topilmadi</div>`; return; }
  box.innerHTML = list.map((p, i) => {
    const qty = Number(p.qty) || 0;
    const bc = String(p.barcode || '').trim();
    const whole = wholesaleSum(p);
    const isTarget = bindMode && bindTarget && bindTarget.id === p.id;
    return `<div class="product-row ${i % 2 ? 'zebra' : ''}${isTarget ? ' bind-target' : ''}" data-id="${p.id}">
      <span class="pr-sku">${p.id}</span>
      <span class="pr-name">${escapeHtml(p.name)}${bc ? `<span class="pr-barcode">${bc}</span>` : ''}</span>
      <span class="${qty <= 0 ? 'pr-qty out' : 'pr-qty'}">${nf(qty)}</span>
      <span class="pr-price">${nf(priceSum(p))}</span>
      <span class="pr-whole">${whole ? nf(whole) : '—'}</span>
    </div>`;
  }).join('');
  box.querySelectorAll('.product-row').forEach((el) =>
    el.addEventListener('click', () => {
      const pid = Number(el.dataset.id);
      if (bindMode) setBindTarget(CATALOG.find((x) => x.id === pid) || null);
      else addToCart(pid);
    }));
}

// ── Shtrix-kod biriktirish rejimi ───────────────────────────────────────
function toggleBindMode() {
  bindMode = !bindMode;
  bindTarget = null;
  $('bindBtn').classList.toggle('bind-on', bindMode);
  const scan = $('scanInput');
  if (scan) scan.placeholder = bindMode
    ? 'Tovarni tanlang yoki SKU/nom yozing · keyin shtrixni skanerlang'
    : "Nomi yoki SKU bilan qidiring · shtrix-kodni skaner qiling";
  updateBindBar();
  renderList('');
  if (scan) scan.value = '';
  focusScan();
  toast(bindMode ? '🔗 Shtrix biriktirish YONIQ' : 'Shtrix biriktirish o\'chdi');
}
function setBindTarget(p) {
  if (!p) return;
  bindTarget = p;
  const scan = $('scanInput');
  if (scan) scan.value = '';
  updateBindBar();
  renderList('');
  focusScan();
}
function clearBindTarget() {
  bindTarget = null;
  const scan = $('scanInput');
  if (scan) scan.value = '';
  updateBindBar();
  renderList('');
  focusScan();
}
function updateBindBar() {
  const bar = $('bindBar');
  if (!bar) return;
  if (!bindMode) { bar.classList.add('hidden'); bar.innerHTML = ''; return; }
  bar.classList.remove('hidden');
  if (!bindTarget) {
    bar.innerHTML = `<span class="bb-ico">🔗</span><span><b>Shtrix biriktirish</b> — qaysi tovarga biriktirasiz? Ro'yxatdan tanlang yoki SKU/nom yozib Enter bosing.</span>`;
    return;
  }
  const bc = String(bindTarget.barcode || '').trim();
  bar.innerHTML = `<span class="bb-ico">🔗</span>`
    + `<span>Tovar: <b>${escapeHtml(bindTarget.name)}</b> (SKU ${bindTarget.id})`
    + (bc ? ` · joriy shtrix: <b>${escapeHtml(bc)}</b>` : ` · shtrix yo'q`)
    + ` → endi yangi <b>shtrixni skanerlang</b> yoki yozib Enter bosing.</span>`
    + `<button class="bb-cancel" id="bindCancel">✕ Bekor</button>`;
  const cb = $('bindCancel');
  if (cb) cb.addEventListener('click', clearBindTarget);
}
async function doBindBarcode(code) {
  code = String(code || '').trim();
  if (!bindTarget) { toast('Avval tovarni tanlang', true); return; }
  if (!code) { toast('Shtrixni skanerlang yoki yozing', true); return; }
  const res = await window.kassa.setProductBarcode(bindTarget.id, code);
  if (!res || !res.ok) { toast('❌ ' + ((res && res.error) || 'saqlanmadi'), true); return; }
  const cp = CATALOG.find((x) => x.id === bindTarget.id);
  if (cp) cp.barcode = code;                 // lokal katalogni darrov yangilaymiz (skaner topishi uchun)
  toast(`🔗 «${bindTarget.name}» ← ${code}`);
  bindTarget = null;
  const scan = $('scanInput');
  if (scan) scan.value = '';
  updateBindBar();
  renderList('');
  focusScan();
}

// ── Savat ──────────────────────────────────────────────────────────────
function addToCart(pid) {
  const p = CATALOG.find((x) => x.id === pid);
  if (!p) return;
  const idx = CART.findIndex((c) => c.product_id === pid);
  if (idx >= 0) { CART[idx].qty += 1; selIndex = idx; }
  else {
    CART.unshift({
      product_id: pid, name: p.name, sku: p.id, barcode: String(p.barcode || ''),
      qty: 1, price_sum: priceSum(p), orig: priceSum(p),
      unit: p.unit || 'dona', qoldiq: Number(p.qty) || 0,
    });
    selIndex = 0;
  }
  keypadMode = 'qty'; setMode('qty'); resetKp(); clearOverride();
  // Qidiruv maydoni doim tozalanadi
  $('scanInput').value = '';
  renderList('');
  renderCart();
  focusScan();
}
function lineSum(c) { return Math.max(0, c.price_sum * c.qty); }
function cartTotal() { return CART.reduce((a, c) => a + lineSum(c), 0); }
function effectiveTotal() { return overrideTotal != null ? overrideTotal : cartTotal(); }
function clearOverride() { overrideTotal = null; }
// Umumiy chegirmani har bir qatorga proporsional yoyadi (yig'indi aniq jamiga teng)
function effLineSums() {
  const naturals = CART.map(lineSum);
  const base = naturals.reduce((a, b) => a + b, 0);
  const total = effectiveTotal();
  if (base <= 0 || total === base) return naturals.slice();
  const raw = naturals.map((n) => n * total / base);
  const res = raw.map(Math.floor);
  let rem = Math.round(total - res.reduce((a, b) => a + b, 0));
  const order = raw.map((v, i) => ({ i, f: v - Math.floor(v) })).sort((a, b) => b.f - a.f);
  for (let k = 0; k < rem && k < order.length; k++) res[order[k].i] += 1;
  return res;
}
function origOf(c) { return Number(c.orig) || Number(c.price_sum) || 0; }
function cartOrigTotal() { return CART.reduce((a, c) => a + origOf(c) * c.qty, 0); }
// Chegirma foizi: musbat = chegirma (narx tushgan), manfiy = ustama (narx oshgan)
function discPct(c) {
  const o = origOf(c);
  if (o <= 0) return 0;
  return Math.round((o - c.price_sum) / o * 100);
}
function discCell(c) {
  const p = discPct(c);
  return p === 0 ? '—' : p + '%';
}

// ── Ochiq chek tab'lari ─────────────────────────────────────────────────
function syncActive() {
  TABS[activeTab] = { items: CART, client: selClient, override: overrideTotal };
  const cur = TABS[activeTab];
  // Bo'sh (aktiv bo'lmagan) cheklar o'chib ketadi
  TABS = TABS.filter((t) => t === cur || (t.items && t.items.length));
  activeTab = TABS.indexOf(cur);
}
function persistTabs() {
  clearTimeout(persistTabsTimer);
  persistTabsTimer = setTimeout(() => { try { window.kassa.saveTabs(TABS); } catch (_) {} }, 400);
}
function renderTabs() {
  const bar = $('checkTabs');
  if (!bar) return;
  bar.innerHTML = TABS.map((t, i) => {
    const active = i === activeTab;
    const cnt = (t.items || []).length;
    return `<button class="check-tab ${active ? 'active' : ''}" data-i="${i}">` +
           `${active ? '✓ ' : ''}Chek №${i + 1}${cnt ? ` <span class="ct-cnt">${cnt}</span>` : ''}</button>`;
  }).join('') + `<button class="check-tab new" id="newCheckBtn">+ Yangi chek</button>`;
  bar.querySelectorAll('.check-tab[data-i]').forEach((b) =>
    b.addEventListener('click', () => activate(Number(b.dataset.i))));
  $('newCheckBtn').addEventListener('click', newCheck);
}
function activate(i) {
  if (i === activeTab) { focusScan(); return; }
  syncActive();
  if (i < 0 || i >= TABS.length) return;
  activeTab = i;
  const t = TABS[i];
  CART = t.items || [];
  selClient = t.client || null;
  overrideTotal = (t.override != null) ? t.override : null;
  selIndex = CART.length ? 0 : -1;
  resetKp(); updateClientChip(); renderCart(); focusScan();
}
function newCheck() {
  syncActive();
  if (!CART.length) { toast('Bu chek bo\'sh — yangi ochish shart emas'); return; }
  TABS.push({ items: [], client: null, override: null });
  activeTab = TABS.length - 1;
  CART = TABS[activeTab].items; selClient = null; overrideTotal = null; selIndex = -1;
  resetKp(); updateClientChip(); renderCart(); focusScan();
  toast('🧾 Yangi chek ochildi');
}
async function loadTabs() {
  let saved = [];
  try { saved = await window.kassa.getTabs(); } catch (_) {}
  TABS = (Array.isArray(saved) && saved.length) ? saved : [{ items: [], client: null, override: null }];
  activeTab = 0;
  const t = TABS[0];
  CART = t.items || []; selClient = t.client || null;
  overrideTotal = (t.override != null) ? t.override : null;
  selIndex = CART.length ? 0 : -1;
  updateClientChip();
}

function renderCart() {
  syncActive();
  renderTabs();
  persistTabs();
  const body = $('cartBody');
  if (!CART.length) {
    selIndex = -1;
    body.innerHTML = `<div class="cart-empty">Savat bo'sh — mahsulot qo'shing</div>`;
  } else {
    if (selIndex < 0 || selIndex >= CART.length) selIndex = 0;
    const effLines = effLineSums();
    body.innerHTML = CART.map((c, i) => {
      const no = CART.length - i;
      const sub = c.sku + (c.barcode ? ' / ' + c.barcode : ' / -');
      const eff = effLines[i];                       // qatorning chegirmadan keyingi jami
      const origLine = origOf(c) * c.qty;            // asl (katalog) narx bo'yicha
      const effUnit = c.qty ? Math.round(eff / c.qty) : eff;
      const isDisc = eff < origLine - 0.5;           // chegirma berilganmi
      const priceCell = isDisc
        ? `<span class="old-price">${nf(origOf(c))}</span><span class="new-price">${nf(effUnit)}</span>`
        : `<span class="new-price">${nf(effUnit)}</span>`;
      const pct = origLine > 0 ? Math.round((origLine - eff) / origLine * 100) : 0;
      const discTxt = pct === 0 ? '—' : pct + '%';
      return `<div class="cart-row ${i === selIndex ? 'selected' : ''}" data-i="${i}">
        <span class="cr-num">${no}</span>
        <span class="c-name cr-name">
          <span class="cr-title">${escapeHtml(c.name)}</span>
          <span class="cr-sub">${escapeHtml(sub)}</span>
        </span>
        <span class="cr-price">${priceCell}</span>
        <span class="cr-qty">${c.qty}</span>
        <span class="cr-stock">${nf(c.qoldiq)}</span>
        <span class="cr-disc">${discTxt}</span>
        <span class="cr-sum">${nf(eff)}</span>
      </div>`;
    }).join('');
    body.querySelectorAll('.cart-row').forEach((el) =>
      el.addEventListener('click', () => {
        selIndex = Number(el.dataset.i); resetKp();
        renderCart();
        openEditModal(selIndex);
      }));
    const selEl = body.querySelector('.cart-row.selected');
    if (selEl) selEl.scrollIntoView({ block: 'nearest' });
  }
  $('grandTotal').textContent = nf(effectiveTotal());
  updateDiscLine();
  $('simpleBtn').disabled = !CART.length;
  $('payQrBtn').disabled = !CART.length;
}

function updateDiscLine() {
  const el = $('discLine');
  const orig = cartOrigTotal();
  const now = effectiveTotal();
  const diff = orig - now;  // >0 = chegirma, <0 = ustama
  if (!CART.length || diff === 0) { el.classList.add('hidden'); return; }
  const pct = orig > 0 ? Math.round(diff / orig * 100) : 0;
  el.classList.remove('hidden');
  if (diff > 0) {
    el.className = 'disc-line discount';
    el.textContent = `Chegirma: ${pct}%  (−${nf(diff)} so'm)`;
  } else {
    el.className = 'disc-line markup';
    el.textContent = `Chegirma: ${pct}%  (+${nf(-diff)} so'm)`;
  }
}

function moveSel(delta) {
  if (!CART.length) return;
  selIndex = Math.max(0, Math.min(CART.length - 1, selIndex + delta));
  resetKp(); renderCart();
}
function bumpQty(delta) {
  if (selIndex < 0 || !CART[selIndex]) return;
  CART[selIndex].qty = Math.max(1, CART[selIndex].qty + delta);
  resetKp(); clearOverride(); renderCart();
}
function delSelected() {
  if (selIndex < 0 || !CART[selIndex]) return;
  CART.splice(selIndex, 1);
  if (selIndex >= CART.length) selIndex = CART.length - 1;
  resetKp(); clearOverride(); renderCart(); focusScan();
}

// ── Tahrirlash modal ────────────────────────────────────────────────────
function openEditModal(i) {
  const c = CART[i]; if (!c) return;
  editIndex = i;
  $('editName').textContent = c.name;
  $('editPrice').value = c.price_sum;
  $('editQty').value = c.qty;
  updateEditTotal();
  openModal('editModal');
  setTimeout(() => { $('editPrice').focus(); $('editPrice').select(); }, 60);
}
function updateEditTotal() {
  const c = CART[editIndex] || {};
  const orig = origOf(c);
  const p = Number($('editPrice').value) || 0;
  const q = Number($('editQty').value) || 0;
  $('editTotal').textContent = nf(Math.max(0, p * q));
  const info = $('editDiscInfo');
  const pct = orig > 0 ? Math.round((orig - p) / orig * 100) : 0;
  const diff = (orig - p) * q;
  if (pct === 0) { info.className = 'edit-disc'; info.textContent = 'Chegirma: —'; }
  else if (pct > 0) { info.className = 'edit-disc discount'; info.textContent = `Chegirma: ${pct}%  (−${nf(diff)} so'm)`; }
  else { info.className = 'edit-disc markup'; info.textContent = `Chegirma: ${pct}%  (+${nf(-diff)} so'm)`; }
}
function saveEdit() {
  const c = CART[editIndex];
  if (c) {
    c.price_sum = Math.max(0, Number($('editPrice').value) || 0);
    c.qty = Math.max(1, Number($('editQty').value) || 1);
    clearOverride();
  }
  closeModal('editModal'); renderCart(); focusScan();
}

// ── Umumiy summa modal ──────────────────────────────────────────────────
function openTotalModal() {
  if (!CART.length) { toast('Savat bo\'sh', true); return; }
  $('totalNatural').textContent = 'Hozirgi: ' + nf(cartTotal()) + ' so\'m';
  $('totalInput').value = effectiveTotal();
  updateTotalDisc();
  openModal('totalModal');
  setTimeout(() => { $('totalInput').focus(); $('totalInput').select(); }, 60);
}
function updateTotalDisc() {
  const base = cartTotal();
  const v = Number($('totalInput').value) || 0;
  const info = $('totalDiscInfo');
  const diff = base - v;
  const pct = base > 0 ? Math.round(diff / base * 100) : 0;
  if (diff === 0) { info.className = 'edit-disc'; info.textContent = 'Chegirma: —'; }
  else if (diff > 0) { info.className = 'edit-disc discount'; info.textContent = `Chegirma: ${pct}%  (−${nf(diff)} so'm)`; }
  else { info.className = 'edit-disc markup'; info.textContent = `Chegirma: ${pct}%  (+${nf(-diff)} so'm)`; }
}
function saveTotal() {
  const v = Math.max(0, Number($('totalInput').value) || 0);
  overrideTotal = (v === cartTotal()) ? null : v;
  closeModal('totalModal'); renderCart(); focusScan();
}
function resetTotal() { clearOverride(); closeModal('totalModal'); renderCart(); focusScan(); }

// ── Raqamli klaviatura ──────────────────────────────────────────────────
function setMode(mode) {
  keypadMode = mode;
  document.querySelectorAll('.mode-btn').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
  resetKp();
}
function resetKp() { kpBuffer = ''; kpLast = 0; }
function kpDigit(d) {
  if (selIndex < 0 || !CART[selIndex]) { toast('Avval mahsulot tanlang', true); return; }
  const now = Date.now();
  if (now - kpLast > KP_RESET_MS) kpBuffer = '';
  kpLast = now;
  kpBuffer += d;
  applyKp(parseInt(kpBuffer, 10) || 0);
}
function kpBackspace() {
  if (selIndex < 0 || !CART[selIndex]) return;
  kpBuffer = kpBuffer.slice(0, -1);
  applyKp(parseInt(kpBuffer, 10) || 0);
}
function applyKp(val) {
  const c = CART[selIndex]; if (!c) return;
  if (keypadMode === 'qty') c.qty = Math.max(1, val || 1);
  else if (keypadMode === 'price') c.price_sum = Math.max(0, val);
  clearOverride();
  renderCart();
}

// ── To'lov (QR bilan - Telegram bot orqali) ────────────────────────────────
async function sellWithQR() {
  if (!CART.length) return;
  // «Chinor» ichki rasxod yoki qarzga savdoda QR to'lov bo'lmaydi — oddiy yozuv
  if (selClient && selClient.is_internal) { return sell(); }
  if (payMethod === 'nasiya') { toast('🤝 Qarz uchun «Chek» tugmasidan foydalaning', true); return; }
  const subtotal = cartTotal();
  const total = effectiveTotal();
  const cashierName = (window._lastState && window._lastState.cashier && window._lastState.cashier.name) || '';
  // 1) Sotuvni saqlaymiz
  const res = await window.kassa.recordSale({
    items: CART.map((c) => ({
      product_id: c.product_id, name: c.name, sku: c.sku, barcode: c.barcode,
      qty: c.qty, price_sum: c.price_sum, orig: origOf(c),
    })),
    payment: payMethod,
    discountSum: Math.max(0, subtotal - total),
    subtotalSum: subtotal,
    totalSum: total,
    clientId: selClient ? selClient.id : 0,
    clientName: selClient ? selClient.name : '',
  });
  if (!res || !res.ok) {
    toast('❌ Xato: ' + ((res && res.error) || 'saqlanmadi'), true);
    return;
  }
  const saleId = res.sale ? res.sale.local_id || res.sale.server_id || '' : '';
  // 2) Eski xabarlarni tozalaymiz — faqat shu so'rovdan keyin kelgan link qabul qilinadi
  //    (aks holda oldingi to'lovning eski linkiga QR generatsiya qilinardi)
  try { await window.kassa.beginPayment(); } catch (_) {}
  // Botga SMS yuboramiz
  window.kassa.sendPaymentMsg(`💳 Yangi to'lov kutilmoqda!\n💰 Summa: ${nf(total)} so'm\n🧾 ID: ${saleId || '—'}\n📎 Iltimos, to'lov linkini yuboring (60 soniya)`);
  toast('⏳ Telegram botdan link kutilmoqda...');
  // 3) Botdan linkni polling qilamiz (har 2 soniyada, maks 30 marta = 60 soniya)
  let link = '';
  for (let i = 0; i < 30; i++) {
    const secLeft = 60 - (i * 2 + 2);
    if (secLeft === 10 || secLeft === 5) {
      window.kassa.sendPaymentMsg(`⏳ To'lov linki kutilmoqda... ${secLeft} soniya qoldi`);
    }
    await new Promise((r) => setTimeout(r, 2000));
    const poll = await window.kassa.pollPaymentLink();
    if (poll && poll.ok && poll.link) {
      link = poll.link;
      break;
    }
  }
  if (!link) {
    window.kassa.sendPaymentMsg(`⚠ Vaqt tugadi. To'lov linki kelmadi.`);
    toast('⚠ Link kelmadi. Chek QR kodsiz chop etildi', true);
    CART = []; selIndex = -1; selClient = null; clearOverride(); updateClientChip(); resetKp(); renderCart();
    if (res.state) applyState(res.state);
    await loadCatalog();
    focusScan();
    return;
  }
  window.kassa.sendPaymentMsg(`✅ Link qabul qilindi! QR kod bilan chek chop etilmoqda.`);
  toast(`✅ Link olindi → QR kod bilan chop etilmoqda`);
  // 4) Sotuvni yangilab, qrLink ni saqlaymiz
  if (saleId) {
    await window.kassa.updateSaleQr(saleId, link);
  }
  // 5) Chek ma'lumotlari (qrLink bilan — QR kodni buildReceiptHtml o'zi chizadi)
  const saleData = {
    items: CART.map((c) => ({
      product_id: c.product_id, name: c.name, sku: c.sku, barcode: c.barcode,
      qty: c.qty, price_sum: c.price_sum, orig: origOf(c),
    })),
    payment: payMethod,
    total_sum: total,
    subtotal_sum: subtotal,
    discount_sum: Math.max(0, subtotal - total),
    cashier_name: cashierName,
    client_name: selClient ? selClient.name : '',
    created_at: new Date().toISOString(),
    id: saleId,
    receipt_no: (res.sale && res.sale.receipt_no) || '',
    qrLink: link,  // Saqlaymiz - keyin cheklar bo'limida ko'rsatish uchun
  };
  // QR li chek (buildReceiptHtml qrLink bo'lsa QR kodni avtomatik qo'shadi)
  if (!LOGO) { try { LOGO = await window.kassa.getLogo(); } catch (_) {} }
  await window.kassa.printReceipt(buildReceiptHtml(saleData));
  // 5) Tozalaymiz
  CART = []; selIndex = -1; selClient = null; clearOverride(); updateClientChip(); resetKp(); renderCart();
  if (res.state) applyState(res.state);
  await loadCatalog();
  focusScan();
  toast('✅ To\'lov · QR bilan chek chop etildi');
}

// ── Sotuv (oddiy) ──────────────────────────────────────────────────────────
async function sell() {
  if (!CART.length) return;
  // «Chinor» tanlansa — ichki rasxod (tannarxda, server qayta hisoblaydi)
  const isInternal = !!(selClient && selClient.is_internal);
  // Qarzga (nasiya) — faqat ruxsat berilgan mijozga
  const isNasiya = !isInternal && payMethod === 'nasiya';
  if (isNasiya && !(selClient && selClient.allow_credit)) {
    toast('❌ Bu mijozga qarzga savdo ruxsat etilmagan', true);
    return;
  }
  const subtotal = cartTotal();
  const total = effectiveTotal();
  const res = await window.kassa.recordSale({
    items: CART.map((c) => ({
      product_id: c.product_id, name: c.name, sku: c.sku, barcode: c.barcode,
      qty: c.qty, price_sum: c.price_sum, orig: origOf(c),
    })),
    payment: payMethod,
    discountSum: Math.max(0, subtotal - total),
    subtotalSum: subtotal,
    totalSum: total,
    clientId: selClient ? selClient.id : 0,
    clientName: selClient ? selClient.name : '',
    isNasiya,
    isInternal,
  });
  if (res && res.ok) {
    toast(isInternal ? '🔻 Chinor rasxodi yozildi (tannarxda)'
      : (isNasiya ? `🤝 Qarzga yozildi · ${nf(total)} so'm`
      : `✅ Sotildi · ${nf(total)} so'm`));
    // Avtomatik chek chop etish (1x tanlangan bo'lsa). Ichki rasxod uchun
    // mijoz cheki chop etilmaydi.
    if (printOnSell && !isInternal) {
      const sale = {
        items: CART.map((c) => ({
          product_id: c.product_id, name: c.name, sku: c.sku, barcode: c.barcode,
          qty: c.qty, price_sum: c.price_sum, orig: origOf(c),
        })),
        payment: payMethod,
        total_sum: total,
        subtotal_sum: subtotal,
        discount_sum: Math.max(0, subtotal - total),
        cashier_name: (res.state && res.state.cashier && res.state.cashier.name) || '',
        client_name: selClient ? selClient.name : '',
        created_at: (res.sale && res.sale.created_at) || new Date().toISOString(),
        id: (res.sale && res.sale.server_id) || '',
        receipt_no: (res.sale && res.sale.receipt_no) || '',
      };
      printReceipt(sale);
    }
    CART = []; selIndex = -1; selClient = null; clearOverride(); updateClientChip(); resetPayMethod(); resetKp(); renderCart();
    if (res.state) applyState(res.state);
    await loadCatalog();
    focusScan();
  } else {
    toast('❌ Xato: ' + ((res && res.error) || 'saqlanmadi'), true);
  }
}

// ── Kassa qulfi (lock-ekran / Win-lock + topshirish) ───────────────────
let lockPin = '';
let lockCtx = null;            // unlock natijasi: {pin, cashier, expected, prevCashier, lockCount}
let addFirstSetup = false;     // birinchi kassir sozlamasimi
let lockCountExpected = 0;     // Lock modali uchun kutilgan naqd

function lockSetView(name) {
  ['Pin', 'Open', 'Handover', 'Add'].forEach((v) =>
    $('lock' + v + 'View').classList.toggle('hidden', v.toLowerCase() !== name));
}
function renderLockDots() {
  $('lockDots').innerHTML = Array.from({ length: Math.max(4, lockPin.length) }, (_, i) =>
    `<span class="lock-dot${i < lockPin.length ? ' on' : ''}"></span>`).join('');
}
function lockMsg(t) { $('lockMsg').textContent = t || ''; }
function showLock(st) {
  st = st || window._lastState || {};
  lockPin = ''; lockCtx = null;
  $('lockScreen').classList.remove('hidden');
  lockMsg('');
  if (!st.hasCashiers) {
    $('lockTitle').textContent = 'Birinchi kassirni qo\'shing';
    $('lockSub').textContent = 'Kassani ishlatish uchun kamida bitta kassir kerak';
    openAddCashier(true);
  } else {
    $('lockTitle').textContent = 'Kassa qulflangan';
    $('lockSub').textContent = 'Davom etish uchun parolingizni kiriting';
    lockSetView('pin');
    renderLockDots();
  }
}
function hideLock() {
  if ($('lockScreen').classList.contains('hidden')) return;
  $('lockScreen').classList.add('hidden');
  lockPin = ''; lockCtx = null;
  focusScan();
}
function lockDigit(d) { if (lockPin.length >= 6) return; lockPin += String(d); lockMsg(''); renderLockDots(); }
function lockBack() { lockPin = lockPin.slice(0, -1); renderLockDots(); }
async function lockSubmitPin() {
  if (lockPin.length < 4) { lockMsg('Parol kamida 4 raqam'); return; }
  const res = await window.kassa.unlockKassa(lockPin);
  if (!res || !res.ok) { lockMsg((res && res.error) || 'Parol noto\'g\'ri'); lockPin = ''; renderLockDots(); return; }
  if (res.action === 'resume') {
    if (res.state) applyState(res.state);
    hideLock(); toast('🔓 Xush kelibsiz, ' + ((res.cashier && res.cashier.name) || ''));
    return;
  }
  if (res.action === 'openShift') {
    lockCtx = { pin: lockPin, cashier: res.cashier };
    $('lockTitle').textContent = 'Smenani ochish';
    $('lockSub').textContent = ((res.cashier && res.cashier.name) || '') + ' · boshlang\'ich naqdni kiriting';
    $('lockOpenCash').value = '0';
    lockSetView('open');
    setTimeout(() => { $('lockOpenCash').focus(); $('lockOpenCash').select(); }, 30);
    return;
  }
  if (res.action === 'handover') {
    lockCtx = { pin: lockPin, cashier: res.cashier, expected: res.expected || 0,
                prevCashier: res.prevCashier || '', lockCount: res.lockCount || null };
    $('lockTitle').textContent = 'Kassani topshirib olish';
    $('lockSub').textContent = ((res.cashier && res.cashier.name) || '') + ' · kassani sanab oling';
    const lc = res.lockCount;
    $('lockHandoverInfo').innerHTML =
      `Avvalgi kassir: <b>${escapeHtml(res.prevCashier || '—')}</b><br>`
      + `Kassa kutgan naqd: <b>${nf(res.expected || 0)} so'm</b>`
      + (lc ? `<br>Topshirayotgan sanagan: <b>${nf(lc.counted || 0)} so'm</b>` : '');
    $('lockCountCash').value = '';
    $('lockDiff').textContent = ''; $('lockDiff').className = 'lock-diff';
    lockSetView('handover');
    setTimeout(() => $('lockCountCash').focus(), 30);
    return;
  }
}
function lockDiffText(expected, countedRaw, el) {
  if (!el) return;
  const raw = String(countedRaw == null ? '' : countedRaw).trim();
  if (raw === '') { el.textContent = ''; el.className = 'lock-diff'; return; }
  const diff = (Number(raw) || 0) - (Number(expected) || 0);
  if (diff === 0) { el.textContent = '✓ Mos keldi'; el.className = 'lock-diff ok'; }
  else if (diff < 0) { el.textContent = `Kamomad: −${nf(-diff)} so'm`; el.className = 'lock-diff short'; }
  else { el.textContent = `Ortiqcha: +${nf(diff)} so'm`; el.className = 'lock-diff over'; }
}
async function lockOpenConfirm() {
  if (!lockCtx) return;
  const cash = Math.max(0, Number($('lockOpenCash').value) || 0);
  const res = await window.kassa.openShiftManual(lockCtx.pin, cash);
  if (!res || !res.ok) { toast('❌ ' + ((res && res.error) || 'ochilmadi'), true); return; }
  if (res.state) applyState(res.state);
  hideLock(); toast(`✅ Smena ochildi · ${nf(cash)} so'm`);
}
async function lockHandoverConfirm() {
  if (!lockCtx) return;
  const counted = Math.max(0, Number($('lockCountCash').value) || 0);
  const res = await window.kassa.handoverKassa(lockCtx.pin, counted);
  if (!res || !res.ok) { toast('❌ ' + ((res && res.error) || 'topshirilmadi'), true); return; }
  if (res.state) applyState(res.state);
  hideLock();
  const d = counted - (lockCtx.expected || 0);
  toast(d === 0 ? '✅ Kassa topshirildi (mos keldi)'
    : d < 0 ? `⚠️ Topshirildi · kamomad −${nf(-d)} so'm`
    : `Topshirildi · ortiqcha +${nf(d)} so'm`);
}
function openAddCashier(first) {
  addFirstSetup = !!first;
  $('lockNewName').value = '';
  $('lockNewPin').value = '';
  $('lockAuthPin').value = '';
  $('lockAuthRow').classList.toggle('hidden', !!first);   // birinchi kassir — tasdiqlash kerak emas
  lockSetView('add');
  setTimeout(() => $('lockNewName').focus(), 30);
}
async function lockAddConfirm() {
  const name = $('lockNewName').value.trim();
  const pin = $('lockNewPin').value.trim();
  const authPin = $('lockAuthPin').value.trim();
  const res = await window.kassa.addCashier(name, pin, addFirstSetup ? '' : authPin);
  if (!res || !res.ok) { toast('❌ ' + ((res && res.error) || 'qo\'shilmadi'), true); return; }
  toast('✅ Kassir qo\'shildi: ' + ((res.cashier && res.cashier.name) || ''));
  $('lockTitle').textContent = 'Kassa qulflangan';
  $('lockSub').textContent = 'Parolingizni kiriting';
  lockPin = ''; lockSetView('pin'); renderLockDots();
}
// Lock tugmasi → qulflashdan oldin naqdni sanash (1-tekshiruv)
function openLockCount() {
  const sh = (window._lastState && window._lastState.shift) || {};
  if (!sh.open) { toast('Smena ochiq emas', true); return; }
  lockCountExpected = Number(sh.expectedCash) || 0;
  $('lcExpected').textContent = nf(lockCountExpected);
  $('lcCount').value = '';
  $('lcDiff').textContent = ''; $('lcDiff').className = 'lock-diff';
  openModal('lockCountModal');
  setTimeout(() => $('lcCount').focus(), 30);
}
async function confirmLockCount() {
  const counted = Math.max(0, Number($('lcCount').value) || 0);
  const res = await window.kassa.lockKassa(counted);
  closeModal('lockCountModal');
  if (res && res.state) applyState(res.state);   // locked=true → applyState lock-ekranni ko'rsatadi
  toast('🔒 Kassa qulflandi');
}

let toastTimer = null;
function toast(msg, err = false) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast' + (err ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 2400);
}

// ── Login ────────────────────────────────────────────────────────────────
async function doLogin() {
  const login = $('loginInput').value.trim();
  const password = $('passInput').value;
  const server = $('serverInput').value.trim();
  const errEl = $('loginErr');
  errEl.textContent = '';
  if (!login || !password) { errEl.textContent = 'Login va parolni kiriting'; return; }
  if (server) await window.kassa.setServerUrl(server);
  const kassaVal = $('kassaInput') && $('kassaInput').value.trim();
  if (kassaVal) await window.kassa.setKassaNo(kassaVal);
  $('loginBtn').disabled = true; $('loginBtn').textContent = 'Kirilmoqda…';
  const res = await window.kassa.login(login, password);
  $('loginBtn').disabled = false; $('loginBtn').textContent = 'Kirish';
  if (res && res.ok) {
    applyState(res.state);
    await loadCatalog(); await loadClients(); await loadTabs();
    renderCart();
    focusScan();
  } else {
    errEl.textContent = (res && res.error) || 'Kirib bo\'lmadi';
  }
}

// ── Hodisalar ──────────────────────────────────────────────────────────────
function wireEvents() {
  $('loginBtn').addEventListener('click', doLogin);
  $('passInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });

  const scan = $('scanInput');
  scan.addEventListener('input', (e) => {
    const val = e.target.value.trim();
    // Shtrix biriktirish rejimida — savatga qo'shmaymiz
    if (bindMode) {
      if (!bindTarget) renderList(val);   // hali tovar tanlanmagan: ro'yxatni filtrlaymiz
      return;                             // tovar tanlangan bo'lsa: input = shtrix (Enter bilan biriktiriladi)
    }
    // Faqat raqam terilsa — barcode yoki SKU mos kelsa darrov savatga
    if (/^\d+$/.test(val) && val.length >= 3) {
      const pb = byBarcode(val);
      if (pb) { addToCart(pb.id); toast(`+ ${pb.name}`); return; }
      const exact = CATALOG.find((x) => String(x.id) === val);
      // SKU faqat to'liq terilganda qo'shilsin (uzunroq SKU prefiksi bo'lmasa)
      if (exact && !CATALOG.some((x) => { const s = String(x.id); return s.length > val.length && s.startsWith(val); })) {
        addToCart(exact.id); toast(`+ ${exact.name}`); return;
      }
    }
    renderList(val);
  });
  scan.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const val = e.target.value.trim();
    // Shtrix biriktirish rejimi
    if (bindMode) {
      if (!val) return;
      if (bindTarget) { doBindBarcode(val); return; }          // tovar tanlangan → shtrixni biriktiramiz
      let p = CATALOG.find((x) => String(x.id) === val) || byBarcode(val);
      if (!p) { const m = matchProducts(val); if (m.length === 1) p = m[0]; }
      if (p) setBindTarget(p);                                  // tovarni topib target qilamiz
      else toast('Tovar topilmadi — ro\'yxatdan tanlang', true);
      return;
    }
    if (!val) return;
    let p = byBarcode(val) || CATALOG.find((x) => String(x.id) === val);
    if (!p) { const m = matchProducts(val); if (m.length === 1) p = m[0]; }
    if (p) { addToCart(p.id); toast(`+ ${p.name}`); }
    else { toast('❌ Topilmadi: ' + val, true); }
  });

  // Klaviatura: strelkalar + tezkor tugmalar (F1–F9). Login ekranida ishlamaydi.
  document.addEventListener('keydown', (e) => {
    if ($('kassaScreen').classList.contains('hidden')) return;

    // Lock-ekran ochiq: PIN ko'rinishida raqam/Enter/Backspace; boshqa ko'rinishlarda Enter — tasdiqlash
    if (!$('lockScreen').classList.contains('hidden')) {
      if (!$('lockPinView').classList.contains('hidden')) {
        if (/^[0-9]$/.test(e.key)) { e.preventDefault(); lockDigit(e.key); return; }
        if (e.key === 'Backspace') { e.preventDefault(); lockBack(); return; }
        if (e.key === 'Enter') { e.preventDefault(); lockSubmitPin(); return; }
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (!$('lockOpenView').classList.contains('hidden')) lockOpenConfirm();
        else if (!$('lockHandoverView').classList.contains('hidden')) lockHandoverConfirm();
        else if (!$('lockAddView').classList.contains('hidden')) lockAddConfirm();
      }
      return;   // qulflangan — kassa tezkor tugmalari ishlamaydi
    }

    // Tezkor tugmalar yordami ochiq bo'lsa — Esc yopadi
    const help = $('hotkeyHelp');
    if (help && !help.classList.contains('hidden') && e.key === 'Escape') {
      help.classList.add('hidden'); return;
    }

    // Cheklar ekrani ochiq: F3/Esc yopadi
    if (!$('receiptsScreen').classList.contains('hidden')) {
      if (e.key === 'Escape' || e.key === 'F3') { e.preventDefault(); closeReceipts(); }
      return;
    }
    // Modal ochiq: faqat Esc yopadi. (Qaytarish modalida Enter — miqdorni savatga
    // qo'shish uchun, qator inputining o'z ishlovchisida; tasdiqlash faqat tugma bilan.)
    if (anyModalOpen()) {
      if (e.key === 'Escape') document.querySelectorAll('.modal-overlay:not(.hidden)').forEach((m) => m.classList.add('hidden'));
      return;
    }

    // Shtrix biriktirish rejimi: Esc — tanlangan tovarni, yo'q bo'lsa rejimni bekor qiladi
    if (bindMode && e.key === 'Escape') {
      e.preventDefault();
      if (bindTarget) clearBindTarget(); else toggleBindMode();
      return;
    }

    // Tezkor tugmalar (modal/cheklar yopiq)
    switch (e.key) {
      case 'F1': e.preventDefault(); parkDraft(); return;
      case 'F2': e.preventDefault(); openClientModal(); return;
      case 'F3': e.preventDefault(); openReceipts(); return;
      case 'F4': e.preventDefault(); openTotalModal(); return;
      case 'F6': e.preventDefault(); cashCheckout(); return;
      case 'F9': e.preventDefault(); sellWithQR(); return;
      case 'Delete': e.preventDefault(); delSelected(); return;
    }

    if (!CART.length) return;
    if (e.key === 'ArrowUp') { e.preventDefault(); moveSel(-1); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); moveSel(1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); bumpQty(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); bumpQty(-1); }
  });

  document.querySelectorAll('.mode-btn').forEach((b) =>
    b.addEventListener('click', () => { setMode(b.dataset.mode); focusScan(); }));
  document.querySelectorAll('.kp[data-d]').forEach((b) =>
    b.addEventListener('click', () => kpDigit(b.dataset.d)));
  $('kpBack').addEventListener('click', kpBackspace);

  document.querySelectorAll('.pay-toggle').forEach((b) =>
    b.addEventListener('click', () => {
      payMethod = b.dataset.pay;
      document.querySelectorAll('.pay-toggle').forEach((x) => x.classList.toggle('active', x === b));
      focusScan();
    }));

  // Print toggle (1x / —)
  document.querySelectorAll('.print-toggle').forEach((b) =>
    b.addEventListener('click', () => {
      printOnSell = b.dataset.print === '1x';
      document.querySelectorAll('.print-toggle').forEach((x) => x.classList.toggle('active', x === b));
      focusScan();
    }));

  $('delRowBtn').addEventListener('click', delSelected);
  $('simpleBtn').addEventListener('click', sell);
  $('payQrBtn').addEventListener('click', sellWithQR);

  // Klient
  $('clientBtn').addEventListener('click', openClientModal);
  $('clientSearch').addEventListener('input', (e) => renderClientList(e.target.value));
  $('clientClear').addEventListener('click', clearClient);

  // Cheklar / qoralama
  $('checksBtn').addEventListener('click', openReceipts);
  $('draftBtn').addEventListener('click', parkDraft);
  $('rcBack').addEventListener('click', closeReceipts);

  // Qaytarish (refund) — usul tanlash + tasdiqlash + terish tugmalari
  $('returnConfirm').addEventListener('click', confirmReturn);
  document.querySelectorAll('.ret-m').forEach((b) =>
    b.addEventListener('click', () => setReturnMethod(b.dataset.m)));
  $('retAddAll').addEventListener('click', returnAddAll);
  $('retClear').addEventListener('click', returnClearBasket);

  // Smena (shift)
  $('shiftBtn').addEventListener('click', openShiftModal);

  // Shtrix-kod biriktirish rejimi (vkluchatel)
  $('bindBtn').addEventListener('click', toggleBindMode);

  // Kassa qulfi (lock-ekran + Win-lock + topshirish)
  $('lockBtn').addEventListener('click', openLockCount);
  $('lcConfirm').addEventListener('click', confirmLockCount);
  $('lcCount').addEventListener('input', () => lockDiffText(lockCountExpected, $('lcCount').value, $('lcDiff')));
  document.querySelectorAll('#lockScreen .lk[data-d]').forEach((b) =>
    b.addEventListener('click', () => lockDigit(b.dataset.d)));
  document.querySelector('#lockScreen .lk-back').addEventListener('click', lockBack);
  document.querySelector('#lockScreen .lk-ok').addEventListener('click', lockSubmitPin);
  $('lockAddCashier').addEventListener('click', () => openAddCashier(false));
  $('lockOpenConfirm').addEventListener('click', lockOpenConfirm);
  $('lockOpenCancel').addEventListener('click', () => showLock());
  $('lockHandoverConfirm').addEventListener('click', lockHandoverConfirm);
  $('lockHandoverCancel').addEventListener('click', () => showLock());
  $('lockCountCash').addEventListener('input', () =>
    lockDiffText(lockCtx ? lockCtx.expected : 0, $('lockCountCash').value, $('lockDiff')));
  $('lockAddConfirm').addEventListener('click', lockAddConfirm);
  $('lockAddCancel').addEventListener('click', () => {
    if (addFirstSetup) showLock(); else { lockSetView('pin'); lockPin = ''; renderLockDots(); }
  });

  // Tezkor tugmalar yordami
  $('hotkeyBtn').addEventListener('click', () => $('hotkeyHelp').classList.toggle('hidden'));
  $('hotkeyHelp').addEventListener('click', () => $('hotkeyHelp').classList.add('hidden'));
  $('rcPrinter').addEventListener('change', (e) => {
    window.kassa.setPrinter(e.target.value);
    toast(e.target.value ? 'Printer belgilandi' : 'Standart printer');
  });
  document.querySelectorAll('.rc-tab').forEach((b) =>
    b.addEventListener('click', () => setRcTab(b.dataset.tab)));

  // Eski cheklarni qidirish: matn / sana / to'lov turi (oy tugmalari buildRcMonths ichida ulanadi)
  $('rcSearch').addEventListener('input', (e) => {
    rcFilter.q = e.target.value;
    $('rcSearchClear').classList.toggle('hidden', !e.target.value);
    renderRcSales();
  });
  $('rcSearchClear').addEventListener('click', () => {
    $('rcSearch').value = ''; rcFilter.q = '';
    $('rcSearchClear').classList.add('hidden');
    renderRcSales(); $('rcSearch').focus();
  });
  $('rcDate').addEventListener('change', (e) => {
    rcFilter.date = e.target.value;
    if (e.target.value) { rcFilter.month = ''; buildRcMonths(); }   // sana tanlansa — oy tozalanadi
    renderRcSales();
  });
  document.querySelectorAll('#rcPayFilters .rc-chip').forEach((b) =>
    b.addEventListener('click', () => {
      rcFilter.pay = b.dataset.pay;
      document.querySelectorAll('#rcPayFilters .rc-chip').forEach((x) => x.classList.toggle('active', x === b));
      renderRcSales();
    }));

  // Tahrirlash modal — Enter saqlaydi
  ['editPrice', 'editQty'].forEach((id) => {
    $(id).addEventListener('input', updateEditTotal);
    $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveEdit(); } });
  });
  $('editSave').addEventListener('click', saveEdit);

  // Umumiy summa modal
  document.querySelector('.summa-box').addEventListener('click', openTotalModal);
  $('totalInput').addEventListener('input', updateTotalDisc);
  $('totalInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveTotal(); } });
  $('totalSave').addEventListener('click', saveTotal);
  $('totalReset').addEventListener('click', resetTotal);

  // Modal yopish (X, Bekor, fon)
  document.querySelectorAll('[data-close]').forEach((b) =>
    b.addEventListener('click', () => b.closest('.modal-overlay').classList.add('hidden')));
  document.querySelectorAll('.modal-overlay').forEach((ov) =>
    ov.addEventListener('click', (e) => { if (e.target === ov) ov.classList.add('hidden'); }));

  $('syncBtn').addEventListener('click', async () => {
    $('syncBtn').textContent = '⟳ …';
    const st = await window.kassa.syncNow();
    applyState(st); await loadCatalog(); await loadClients();
    $('syncBtn').textContent = '⟳ Sinxron';
    toast(st.online ? '🔄 Sinxronlandi' : '⚠ Aloqa yo\'q', !st.online);
  });
  $('logoutBtn').addEventListener('click', async () => {
    syncActive();
    try { await window.kassa.saveTabs(TABS); } catch (_) {}   // ochiq cheklar saqlanadi
    const st = await window.kassa.logout();
    applyState(st);
  });

  // Yangilanish tugmasi
  $('updateBtn').addEventListener('click', () => {
    const mode = $('updateBtn').dataset.mode;
    if (mode === 'install') window.kassa.installUpdate();
    else if (mode === 'download') { window.kassa.downloadUpdate(); toast('⬇️ Yangilanish yuklanmoqda…'); }
  });

  window.kassa.onState((st) => applyState(st));
}

// ── Ishga tushirish ──────────────────────────────────────────────────────
(async function init() {
  wireEvents();
  const st = await window.kassa.getState();
  applyState(st);
  try { LOGO = await window.kassa.getLogo(); } catch (_) {}
  if (st && st.loggedIn) { await loadCatalog(); await loadClients(); await loadTabs(); focusScan(); }
  else { $('loginInput').focus(); }
  renderCart();
})();
