'use strict';
// Lokal ombor — sof JSON fayl (native modul yo'q, Windows .exe oson yig'iladi).
// Katalog keshi va sotuv navbatini saqlaydi. Internet bo'lmaganda ham ishlaydi.

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const DEFAULT_SERVER = 'https://kassa.chinorpos.com';

function nowTashkent() {
  // Server _now() bilan bir xil format: 'YYYY-MM-DD HH:MM:SS' (UTC+5)
  const d = new Date(Date.now() + 5 * 3600 * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
         `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

class Store {
  constructor(file) {
    this.file = file;
    this.data = this._load();
  }

  _default() {
    return {
      serverUrl: DEFAULT_SERVER,
      token: '',
      printerName: '',         // belgilangan printer (bo'sh = standart)
      cashier: null,           // {id, name}
      authHash: '',            // offlayn login uchun (login+parol xeshi)
      loggedOut: false,        // foydalanuvchi chiqib ketganmi (yumshoq logout)
      usdRate: 12500,
      deviceId: crypto.randomUUID(),
      kassaNo: 1,              // shu qurilmaning kassa raqami (chek raqami prefiksi: "1-15")
      catalog: [],             // [{id,name,barcode,unit,qty,sell_price_sum,sell_price_usd,wholesale_sum,image_url}]
      barcodeOverrides: {},    // kassada biriktirilgan shtrixlar: { "<product_id>": "<barcode>" } — katalog qayta yuklansa ham saqlanadi
      clients: [],             // [{id,name,type,debt_sum}]
      catalogSyncedAt: '',
      sales: [],               // [{local_id,receipt_no,items,payment,discountSum,totalSum,clientId,clientName,created_at,synced,server_id}]
      salesSeq: 0,
      receiptsCache: [],       // barcha qurilmalardan sinxronlangan cheklar (vaqt bo'yicha, ~3 oy)
      receiptsLastTs: '',      // keshda eng so'nggi created_at (inkremental sync uchun)
      drafts: [],              // [{id,items,client,created_at}] — qoralama (parked) cheklar
      draftSeq: 0,
      openChecks: [],          // [{items,client,override}] — ochiq chek tab'lari
      shift: null,             // joriy ochiq smena {id,no,openedAt,openedBy,openCash,cashMoves,lockCount}
      shiftSeq: 0,
      shiftHistory: [],        // yopilgan smenalar (Z-hisobotlar bilan, oxirgi ~50)
      cashiers: [],            // lokal kassirlar: [{id,name,pinHash}] — har biri o'z paroli
      cashierSeq: 0,
      locked: true,            // kassa qulflanganmi (smena ochilmaguncha yoki Lock bosilganda)
    };
  }

  _load() {
    try {
      const raw = fs.readFileSync(this.file, 'utf8');
      const d = JSON.parse(raw);
      const data = Object.assign(this._default(), d);
      // Eski ngrok manzili keshda qolgan bo'lsa — joriy production domeniga
      // o'tamiz (ngrok endi ishlatilmaydi). Foydalanuvchi o'zi boshqa server
      // kiritmagan bo'lsa avtomatik to'g'rilanadi.
      if (/ngrok/i.test(String(data.serverUrl || ''))) {
        data.serverUrl = DEFAULT_SERVER;
      }
      return data;
    } catch {
      return this._default();
    }
  }

  save() {
    try {
      const tmp = this.file + '.tmp';
      fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2));
      fs.renameSync(tmp, this.file);   // atomik yozish — buzilishdan himoya
    } catch (e) {
      console.error('[store] save error:', e);
    }
  }

  // ── Auth / sozlamalar ──────────────────────────────────────────────
  setServerUrl(url) {
    this.data.serverUrl = String(url || '').trim().replace(/\/+$/, '') || DEFAULT_SERVER;
    this.save();
  }
  setSession(token, cashier, usdRate) {
    this.data.token = token || '';
    if (cashier) this.data.cashier = cashier;
    if (usdRate) this.data.usdRate = usdRate;
    this.data.loggedOut = false;
    this.save();
  }
  setAuthHash(hash) { this.data.authHash = hash || ''; this.save(); }
  // Yumshoq chiqish — token/cashier/authHash saqlanadi (offlayn qayta kirish uchun)
  logout() { this.data.loggedOut = true; this.save(); }
  restoreSession() { this.data.loggedOut = false; this.save(); }
  get loggedIn() { return !!this.data.token && !!this.data.cashier && !this.data.loggedOut; }

  // ── Katalog ────────────────────────────────────────────────────────
  setCatalog(products, usdRate, clients) {
    if (Array.isArray(products)) {
      // Tannarx (cost) "yopishqoq": agar yangi katalogda cost bo'lmasa
      // (masalan eski server hali cost_sum yubormasa), eski keshdagi tannarxni
      // saqlab qolamiz — shunda bir marta tushgan tannarx offline yo'qolmaydi.
      const costById = {};
      for (const o of (this.data.catalog || [])) {
        const cs = Number(o.cost_sum) || 0, cu = Number(o.cost_usd) || 0;
        if (cs > 0 || cu > 0) costById[o.id] = { cost_sum: cs, cost_usd: cu };
      }
      for (const p of products) {
        if (!(Number(p.cost_sum) > 0) && costById[p.id]) {
          p.cost_sum = costById[p.id].cost_sum;
          if (!(Number(p.cost_usd) > 0)) p.cost_usd = costById[p.id].cost_usd;
        }
      }
      this.data.catalog = products;
    }
    if (Array.isArray(clients)) this.data.clients = clients;
    if (usdRate) this.data.usdRate = usdRate;
    this._applyBarcodeOverrides();   // serverdan kelgan katalogga lokal shtrixlarni qayta qo'llaymiz
    this.data.catalogSyncedAt = new Date().toISOString();
    this.save();
  }
  // Kassada biriktirilgan shtrixlarni joriy katalogga qo'llaydi (server pulldan keyin chaqiriladi)
  _applyBarcodeOverrides() {
    const ov = this.data.barcodeOverrides || {};
    for (const p of this.data.catalog || []) {
      const b = ov[String(p.id)];
      if (b) p.barcode = b;
    }
  }
  // Mahsulotga shtrix-kod biriktiradi (lokal — offlayn ishlaydi, qurilmada saqlanadi).
  setProductBarcode(productId, barcode) {
    productId = Number(productId);
    barcode = String(barcode || '').trim();
    if (!productId) return { ok: false, error: 'Mahsulot tanlanmagan' };
    if (!barcode) return { ok: false, error: 'Shtrix-kod bo\'sh' };
    // Bu shtrix boshqa mahsulotda bormi? (skanerda chalkashmasligi uchun)
    const clash = (this.data.catalog || []).find((p) =>
      Number(p.id) !== productId && String(p.barcode || '').trim() === barcode);
    if (clash) return { ok: false, error: `Bu shtrix «${clash.name}» da bor` };
    this.data.barcodeOverrides = this.data.barcodeOverrides || {};
    this.data.barcodeOverrides[String(productId)] = barcode;
    const p = (this.data.catalog || []).find((x) => Number(x.id) === productId);
    if (p) p.barcode = barcode;
    this.save();
    return { ok: true, product: p ? { id: p.id, name: p.name, barcode: barcode } : { id: productId, barcode } };
  }
  catalogStaleMinutes() {
    if (!this.data.catalogSyncedAt) return Infinity;
    return (Date.now() - new Date(this.data.catalogSyncedAt).getTime()) / 60000;
  }

  // ── Sotuv ──────────────────────────────────────────────────────────
  addSale({ items, payment, discountSum, subtotalSum, totalSum, clientId, clientName, qrLink, qrLinks, isNasiya, isInternal, split }) {
    this.data.salesSeq += 1;
    const local_id = `${this.data.deviceId.slice(0, 8)}-${this.data.salesSeq}`;
    const receipt_no = `${this.data.kassaNo || 1}-${this.data.salesSeq}`;
    // Aralash (split) to'lov — har turdagi summa alohida {cash,card,click,debt}
    const splitObj = split && typeof split === 'object' ? {
      cash: Number(split.cash) || 0,
      card: Number(split.card) || 0,
      click: Number(split.click) || 0,
      debt: Number(split.debt) || 0,
    } : null;
    const sale = {
      local_id,
      receipt_no,                  // ko'rinadigan chek raqami: "<kassaNo>-<seq>" (qurilmalararo to'qnashmaydi)
      items,                       // [{product_id, name, sku, barcode, qty, price_sum, orig}]
      // payment maydoni ham ko'rinish belgisi: 'rasxod' (Chinor) / 'qarz' (nasiya) / 'split' / cash|card
      payment: isInternal ? 'rasxod' : (splitObj ? 'split' : (isNasiya ? 'qarz' : (payment || 'cash'))),
      split: splitObj,             // aralash to'lov taqsimoti (yoki null)
      isNasiya: !!isNasiya,        // qarzga (nasiya) savdo
      isInternal: !!isInternal,    // «Chinor» ichki rasxod (tannarxda)
      discountSum: Number(discountSum) || 0,
      subtotalSum: Number(subtotalSum) || 0,
      totalSum: Number(totalSum) || 0,
      clientId: Number(clientId) || 0,
      clientName: clientName || '',
      qrLink: qrLink || '',       // QR to'lov linki (bitta — orqaga moslik)
      qrLinks: Array.isArray(qrLinks) ? qrLinks.slice() : (qrLink ? [qrLink] : []),  // bir nechta QR (split)
      created_at: nowTashkent(),
      synced: false,
      server_id: null,
    };
    this.data.sales.push(sale);
    // Lokal qoldiqni kamaytiramiz (faqat ko'rsatish uchun — haqiqiy qoldiq serverda)
    for (const it of items) {
      const p = this.data.catalog.find((x) => x.id === it.product_id);
      if (p) p.qty = Math.max(0, (Number(p.qty) || 0) - (Number(it.qty) || 0));
    }
    this.save();
    return sale;
  }

  // ── Qaytarish (refund) ─────────────────────────────────────────────
  // Sotuvni (qisman/to'liq) qaytaradi: lokal qoldiqni tiklaydi va serverga
  // yuboriladigan "qaytarish" yozuvini sotuv navbatiga qo'shadi (manfiy summa).
  addReturn({ items, method, refundSum, origReceiptNo, origSaleId, clientId, clientName }) {
    this.data.salesSeq += 1;
    const local_id = `${this.data.deviceId.slice(0, 8)}-R${this.data.salesSeq}`;
    const receipt_no = `${this.data.kassaNo || 1}-R${this.data.salesSeq}`;
    const refund = Math.abs(Number(refundSum) || 0);
    const m = ['cash', 'card', 'click', 'debt'].includes(method) ? method : 'cash';
    // Lokal ko'rsatish uchun: manfiy miqdor/summa (hisobotlarda netto)
    const negItems = (items || []).map((it) => ({
      product_id: it.product_id, name: it.name, sku: it.sku, barcode: it.barcode,
      qty: -Math.abs(Number(it.qty) || 0), price_sum: Number(it.price_sum) || 0,
      orig: Number(it.orig) || Number(it.price_sum) || 0,
    }));
    const sale = {
      local_id,
      receipt_no,
      items: negItems,
      payment: 'qaytarish',
      isReturn: true,
      origReceiptNo: origReceiptNo || '',
      origSaleId: origSaleId || '',
      returnMethod: m,                    // pul qaysi usulda qaytarildi: cash|card|click|debt
      returnPayment: m,                   // (eski nom — moslik uchun)
      isNasiya: false,
      isInternal: false,
      discountSum: 0,
      subtotalSum: -refund,
      totalSum: -refund,
      clientId: Number(clientId) || 0,    // qarzdan ayirish uchun mijoz
      clientName: clientName || '',
      qrLink: '',
      created_at: nowTashkent(),
      synced: false,
      server_id: null,
    };
    this.data.sales.push(sale);
    // Lokal qoldiqni tiklaymiz (faqat ko'rsatish uchun — haqiqiy qoldiq serverda)
    for (const it of (items || [])) {
      const p = this.data.catalog.find((x) => x.id === it.product_id);
      if (p) p.qty = (Number(p.qty) || 0) + Math.abs(Number(it.qty) || 0);
    }
    this.save();
    return sale;
  }

  pendingSales() { return this.data.sales.filter((s) => !s.synced); }
  pendingCount() { return this.pendingSales().length; }

  markSynced(local_id, server_id) {
    const s = this.data.sales.find((x) => x.local_id === local_id);
    if (s) { s.synced = true; s.server_id = server_id; }
  }

  // ── Qoralama (parked) cheklar ──────────────────────────────────────
  saveDraft({ items, client }) {
    this.data.draftSeq = (this.data.draftSeq || 0) + 1;
    const d = {
      id: this.data.draftSeq,
      items: items || [],
      client: client || null,
      created_at: nowTashkent(),
    };
    this.data.drafts.push(d);
    this.save();
    return d;
  }
  listDrafts() { return (this.data.drafts || []).slice().reverse(); }
  removeDraft(id) {
    this.data.drafts = (this.data.drafts || []).filter((d) => d.id !== id);
    this.save();
  }

  // ── Ochiq chek tab'lari ────────────────────────────────────────────
  getOpenChecks() { return this.data.openChecks || []; }
  setOpenChecks(list) {
    this.data.openChecks = Array.isArray(list) ? list : [];
    this.save();
  }

  // ── Smena (shift / kassa sessiyasi) ────────────────────────────────
  // Smena — kassa ochilganidan yopilgunigacha bo'lgan davr. Hisobot (Z)
  // shu davrdagi BARCHA lokal sotuv/qaytarishlardan hisoblanadi — shunda
  // kassadagi naqd pul aniq chiqadi. Smena shu QURILMAGA tegishli.
  getShift() { return this.data.shift || null; }
  openShift({ openCash = 0, cashier = null } = {}) {
    if (this.data.shift) return this.data.shift;   // allaqachon ochiq
    this.data.shiftSeq = (this.data.shiftSeq || 0) + 1;
    this.data.shift = {
      id: this.data.shiftSeq,
      no: `${this.data.kassaNo || 1}-S${this.data.shiftSeq}`,
      openedAt: nowTashkent(),
      openedBy: cashier || this.data.cashier || null,
      openCash: Math.max(0, Number(openCash) || 0),   // boshlang'ich naqd (kassadagi pul)
      cashMoves: [],                                  // naqd kirim/chiqim [{type,amount,note,at}]
    };
    this.save();
    return this.data.shift;
  }
  // Smena qo'lda ochiladi — avtomatik OCHILMAYDI (kassir o'z qo'li bilan ochishi shart).
  ensureShift() { return this.data.shift; }
  addCashMove({ type, amount, note }) {
    if (!this.data.shift) return null;
    const mv = {
      type: type === 'out' ? 'out' : 'in',
      amount: Math.abs(Number(amount) || 0),
      note: note || '',
      at: nowTashkent(),
    };
    if (mv.amount <= 0) return null;
    this.data.shift.cashMoves.push(mv);
    this.save();
    return mv;
  }
  // Smena hisobotini (X yoki Z) lokal sotuvlardan hisoblaydi
  shiftSummary(shift) {
    const sh = shift || this.data.shift;
    if (!sh) return null;
    const from = sh.openedAt || '';
    const to = sh.closedAt || '9999-99-99 99:99:99';
    const z = {
      no: sh.no, openedAt: sh.openedAt, closedAt: sh.closedAt || '',
      openedBy: sh.openedBy || this.data.cashier || null,
      kassaNo: this.data.kassaNo || 1,
      openCash: Number(sh.openCash) || 0,
      sales: { count: 0, total: 0, cash: 0, card: 0, click: 0, nasiya: 0 },
      returns: { count: 0, total: 0, cash: 0, card: 0, click: 0, debt: 0 },
      internal: { count: 0, total: 0 },
      cashIn: 0, cashOut: 0,
      cashMoves: sh.cashMoves || [],
    };
    for (const s of this.data.sales) {
      const t = s.created_at || '';
      if (t < from || t > to) continue;
      const amt = Number(s.totalSum) || 0;
      if (s.isInternal) { z.internal.count++; z.internal.total += amt; continue; }
      if (s.isReturn) {
        const m = s.returnMethod || s.returnPayment || 'cash';
        const a = Math.abs(amt);
        z.returns.count++; z.returns.total += a;
        if (m === 'debt') z.returns.debt += a;
        else if (m === 'card') z.returns.card += a;
        else if (m === 'click') z.returns.click += a;
        else z.returns.cash += a;
        continue;
      }
      z.sales.count++; z.sales.total += amt;
      if (s.split) {
        // Aralash to'lov — har qism o'z chelagiga (naqd qismi alohida → naqd hisobot to'g'ri)
        z.sales.cash += Number(s.split.cash) || 0;
        z.sales.card += Number(s.split.card) || 0;
        z.sales.click += Number(s.split.click) || 0;
        z.sales.nasiya += Number(s.split.debt) || 0;
      } else {
        const p = s.payment || 'cash';
        if (s.isNasiya || p === 'qarz') z.sales.nasiya += amt;
        else if (p === 'card') z.sales.card += amt;
        else if (p === 'click') z.sales.click += amt;
        else z.sales.cash += amt;
      }
    }
    for (const mv of (sh.cashMoves || [])) {
      if (mv.type === 'out') z.cashOut += Number(mv.amount) || 0;
      else z.cashIn += Number(mv.amount) || 0;
    }
    // Kassadagi kutilayotgan naqd = boshlang'ich + naqd sotuv − naqd qaytarish + kirim − chiqim
    z.expectedCash = z.openCash + z.sales.cash - z.returns.cash + z.cashIn - z.cashOut;
    return z;
  }
  closeShift(countedCash) {
    if (!this.data.shift) return null;
    this.data.shift.closedAt = nowTashkent();
    const z = this.shiftSummary(this.data.shift);
    if (countedCash !== undefined && countedCash !== null) {
      z.countedCash = Math.max(0, Number(countedCash) || 0);
      z.cashDiff = z.countedCash - z.expectedCash;   // + ortiqcha / − kamomad
    }
    if (this.data.shift.lockCount) z.lockCount = this.data.shift.lockCount;  // qulflashdagi sanoq (1-tekshiruv)
    this.data.shiftHistory = this.data.shiftHistory || [];
    this.data.shiftHistory.unshift({ ...this.data.shift, summary: z });
    this.data.shiftHistory = this.data.shiftHistory.slice(0, 50);
    this.data.shift = null;
    this.save();
    return z;
  }

  // ── Kassirlar (lokal) + kassa qulfi ─────────────────────────────────
  _pinHash(pin) {
    return crypto.createHash('sha256')
      .update((this.data.deviceId || 'kassa') + ':' + String(pin || '')).digest('hex');
  }
  hasCashiers() { return (this.data.cashiers || []).length > 0; }
  listCashiers() { return (this.data.cashiers || []).map((c) => ({ id: c.id, name: c.name })); }
  addCashier(name, pin) {
    name = String(name || '').trim();
    pin = String(pin || '').trim();
    if (!name) return { ok: false, error: 'Ism bo\'sh' };
    if (!/^\d{4,6}$/.test(pin)) return { ok: false, error: 'Parol 4–6 raqam bo\'lishi kerak' };
    const hash = this._pinHash(pin);
    if ((this.data.cashiers || []).some((c) => c.pinHash === hash))
      return { ok: false, error: 'Bu parol band — boshqa parol tanlang' };
    this.data.cashierSeq = (this.data.cashierSeq || 0) + 1;
    const c = { id: this.data.cashierSeq, name, pinHash: hash };
    this.data.cashiers = this.data.cashiers || [];
    this.data.cashiers.push(c);
    this.save();
    return { ok: true, cashier: { id: c.id, name: c.name } };
  }
  removeCashier(id) {
    id = Number(id);
    this.data.cashiers = (this.data.cashiers || []).filter((c) => c.id !== id);
    this.save();
    return { ok: true };
  }
  verifyPin(pin) {
    const hash = this._pinHash(String(pin || '').trim());
    const c = (this.data.cashiers || []).find((x) => x.pinHash === hash);
    return c ? { id: c.id, name: c.name } : null;
  }
  isLocked() { return !!this.data.locked; }
  // Qulflashdan oldin kassir naqdni sanaydi (1-tekshiruv). Smena ochiq qoladi.
  lockKassa({ counted } = {}) {
    if (this.data.shift) {
      const z = this.shiftSummary(this.data.shift);
      this.data.shift.lockCount = {
        expected: z ? z.expectedCash : 0,
        counted: Math.max(0, Number(counted) || 0),
        by: this.data.cashier || null,
        at: nowTashkent(),
      };
    }
    this.data.locked = true;
    this.save();
    return { ok: true };
  }
  // O'sha kassir qaytdi — shunchaki davom etadi (qayta sanash yo'q).
  resume(cashier) {
    if (cashier) this.data.cashier = { id: cashier.id, name: cashier.name };
    this.data.locked = false;
    if (this.data.shift && (!this.data.shift.openedBy || !this.data.shift.openedBy.id) && cashier) {
      this.data.shift.openedBy = { id: cashier.id, name: cashier.name };   // egasiz eski smenani egalaymiz
    }
    this.data.shift && (this.data.shift.lockCount = null);
    this.save();
    return { ok: true };
  }
  // Smenani qo'lda ochish (boshlang'ich naqd bilan).
  openShiftFor(cashier, openCash) {
    if (this.data.shift) this.closeShift();
    this.data.cashier = { id: cashier.id, name: cashier.name };
    const sh = this.openShift({ openCash, cashier: { id: cashier.id, name: cashier.name } });
    this.data.locked = false;
    this.save();
    return { ok: true, shift: sh };
  }
  // Topshirish: eski smena sanab olingan summa bilan yopiladi (Z), yangi kassirga yangi smena.
  handover(newCashier, countedCash) {
    countedCash = Math.max(0, Number(countedCash) || 0);
    const z = this.closeShift(countedCash);
    this.data.cashier = { id: newCashier.id, name: newCashier.name };
    const sh = this.openShift({ openCash: countedCash, cashier: { id: newCashier.id, name: newCashier.name } });
    this.data.locked = false;
    this.save();
    return { ok: true, z, shift: sh };
  }
  // Yangilanish tozalash: lokal kassir yo'q bo'lsa — eski avto-smenani tashlaymiz va albatta qulflaymiz.
  migrateLockState() {
    if (this.data.locked === undefined || this.data.locked === null) this.data.locked = true;
    if (!this.hasCashiers()) {
      if (this.data.shift) this.closeShift();   // egasiz avto-smena
      this.data.locked = true;                  // kassir yo'q — kassa albatta qulf
      this.save();
    }
    return true;
  }

  // Bugungi sotuvlar (lokal) — kassa ekranida ko'rsatish uchun
  todaySales() {
    const today = nowTashkent().slice(0, 10);
    return this.data.sales
      .filter((s) => (s.created_at || '').startsWith(today))
      .slice()
      .reverse();
  }
  todayTotal() {
    return this.todaySales().reduce((a, s) => a + (Number(s.totalSum) || 0), 0);
  }

  // ── Kassa raqami ───────────────────────────────────────────────────
  setKassaNo(n) {
    const v = Math.max(1, Math.min(999, parseInt(n, 10) || 1));
    this.data.kassaNo = v;
    this.save();
    return v;
  }

  // ── Cheklar keshi (qurilmalararo sinxron, ~3 oy) ───────────────────
  // Serverdan kelgan cheklarni keshga qo'shadi: dublikatlarni olib tashlaydi
  // (receipt_no bo'yicha, bo'lmasa server id), vaqt bo'yicha tartiblaydi va
  // 90 kundan eski yozuvlarni tashlab yuboradi.
  mergeReceipts(list) {
    if (!Array.isArray(list) || !list.length) return;
    const byKey = new Map();
    // Global noyob kalit: serverdagi id bo'yicha (qurilmalararo TO'QNASHMAYDI —
    // shu sabab boshqa qurilmaning "1-5" cheki ustidan yozilmaydi). Hali
    // yuborilmagan lokal sotuv id'siz → receipt_no/local_id (o'z qurilmasida noyob).
    const keyOf = (s) => {
      const sid = (s.id != null && s.id !== '') ? s.id : s.server_id;
      if (sid != null && sid !== '') return '#' + sid;
      return 'R:' + String(s.receipt_no || s.local_id || '');
    };
    for (const s of this.data.receiptsCache) byKey.set(keyOf(s), s);
    for (const s of list) byKey.set(keyOf(s), s);   // yangisi eskisini almashtiradi
    let merged = Array.from(byKey.values());
    // 90 kundan eski'larni tashlaymiz (created_at — Toshkent vaqti 'YYYY-MM-DD HH:MM:SS')
    const d = new Date(Date.now() - 90 * 24 * 3600 * 1000 + 5 * 3600 * 1000);
    const cutoffStr = d.toISOString().slice(0, 19).replace('T', ' ');
    merged = merged.filter((s) => (s.created_at || '') >= cutoffStr);
    // vaqt bo'yicha kamayuvchi (yangi tepada)
    merged.sort((a, b) => {
      // Barqaror tartib: avval vaqt (yangi tepada), bir xil vaqtda — global
      // noyob server id bo'yicha. Shunda BARCHA qurilmalarda cheklar AYNAN bir
      // xil tartibda chiqadi (bir xil soniyada urilgan cheklar chalkashmaydi).
      const t = String(b.created_at || '').localeCompare(String(a.created_at || ''));
      if (t !== 0) return t;
      const ai = Number(a.id != null ? a.id : (a.server_id || 0)) || 0;
      const bi = Number(b.id != null ? b.id : (b.server_id || 0)) || 0;
      return bi - ai;
    });
    this.data.receiptsCache = merged.slice(0, 3000);
    // eng so'nggi created_at — keyingi inkremental sync uchun
    this.data.receiptsLastTs = merged.reduce(
      (mx, s) => (s.created_at > mx ? s.created_at : mx), this.data.receiptsLastTs || '');
    this.save();
  }

  // Cheklar bo'limi uchun: keshlangan (sinxron) cheklar + hali yuborilmagan
  // lokal sotuvlar, receipt_no bo'yicha dublikatsiz, vaqt bo'yicha tartiblangan.
  getReceiptsMerged() {
    const byKey = new Map();
    // Global noyob kalit: serverdagi id bo'yicha (qurilmalararo TO'QNASHMAYDI —
    // shu sabab boshqa qurilmaning "1-5" cheki ustidan yozilmaydi). Hali
    // yuborilmagan lokal sotuv id'siz → receipt_no/local_id (o'z qurilmasida noyob).
    const keyOf = (s) => {
      const sid = (s.id != null && s.id !== '') ? s.id : s.server_id;
      if (sid != null && sid !== '') return '#' + sid;
      return 'R:' + String(s.receipt_no || s.local_id || '');
    };
    for (const s of this.data.receiptsCache || []) byKey.set(keyOf(s), s);
    // hali sinxronlanmagan lokal sotuvlarni "chek" ko'rinishiga keltiramiz
    for (const s of this.pendingSales()) {
      const r = {
        id: s.server_id || null,
        receipt_no: s.receipt_no || '',
        created_at: s.created_at,
        cashier_name: (this.data.cashier && this.data.cashier.name) || '',
        source: '',
        total_sum: Number(s.totalSum) || 0,
        subtotal_sum: Number(s.subtotalSum) || 0,
        discount_sum: Number(s.discountSum) || 0,
        payment: s.payment || 'cash',
        is_return: s.isReturn ? 1 : 0,
        is_nasiya: s.isNasiya ? 1 : 0,
        orig_receipt_no: s.origReceiptNo || '',
        client_id: s.clientId || 0,
        client_name: s.clientName || '',
        qrLink: s.qrLink || '',
        qrLinks: Array.isArray(s.qrLinks) ? s.qrLinks : (s.qrLink ? [s.qrLink] : []),
        split: s.split || null,
        items: (s.items || []).map((it) => ({
          name: it.name, qty: it.qty, price_sum: it.price_sum,
          product_id: it.product_id, sku: it.sku, barcode: it.barcode, orig: it.orig,
        })),
      };
      byKey.set(keyOf(r), r);   // pending o'zining keshdagi nusxasini ustlaydi
    }
    // QR linkni saqlab qolish: lokal sotuvlardan qrLink/qrLinks/split ni olish
    // (serverdan kelgan versiyada bo'lmasligi mumkin)
    for (const s of this.data.sales || []) {
      if (s.qrLink || (s.qrLinks && s.qrLinks.length) || s.split) {
        const key = keyOf(s);
        const existing = byKey.get(key);
        if (existing) {
          if (!existing.qrLink && s.qrLink) existing.qrLink = s.qrLink;
          if ((!existing.qrLinks || !existing.qrLinks.length) && s.qrLinks && s.qrLinks.length) {
            existing.qrLinks = s.qrLinks.slice();
          }
          if (!existing.split && s.split) existing.split = s.split;
        }
      }
    }
    return Array.from(byKey.values())
      .sort((a, b) => {
      // Barqaror tartib: avval vaqt (yangi tepada), bir xil vaqtda — global
      // noyob server id bo'yicha. Shunda BARCHA qurilmalarda cheklar AYNAN bir
      // xil tartibda chiqadi (bir xil soniyada urilgan cheklar chalkashmaydi).
      const t = String(b.created_at || '').localeCompare(String(a.created_at || ''));
      if (t !== 0) return t;
      const ai = Number(a.id != null ? a.id : (a.server_id || 0)) || 0;
      const bi = Number(b.id != null ? b.id : (b.server_id || 0)) || 0;
      return bi - ai;
    });
  }
}

module.exports = { Store, nowTashkent };
