'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// Renderer (UI) faqat shu xavfsiz API orqali main process bilan gaplashadi.
contextBridge.exposeInMainWorld('chinor', {
  getState: () => ipcRenderer.invoke('state:get'),
  setServerUrl: (url) => ipcRenderer.invoke('settings:server', url),
  health: () => ipcRenderer.invoke('net:health'),
  login: (server, login, password) => ipcRenderer.invoke('auth:login', { server, login, password }),
  logout: () => ipcRenderer.invoke('auth:logout'),
  // Hisobot ma'lumotlari (jonli, serverdan)
  getStats: () => ipcRenderer.invoke('data:stats'),
  getRecentSales: (since) => ipcRenderer.invoke('data:recentSales', since),
  getSalesSummary: (days) => ipcRenderer.invoke('data:salesSummary', days),
  getOrders: () => ipcRenderer.invoke('data:orders'),
  getProducts: (page, limit) => ipcRenderer.invoke('data:products', { page: page || 0, limit: limit || 0 }),
  productQty: (id, delta) => ipcRenderer.invoke('data:productQty', { id, delta }),
  productSave: (prod) => ipcRenderer.invoke('data:productSave', prod),
  prixodScan: (bytes, filename) => ipcRenderer.invoke('data:prixodScan', { bytes, filename }),
  prixodList: (from, to) => ipcRenderer.invoke('data:prixodList', { from, to }),
  prixodSave: (payload) => ipcRenderer.invoke('data:prixodSave', payload),
  getPrinters: () => ipcRenderer.invoke('printers:list'),
  saveLabelCfg: (cfg) => ipcRenderer.invoke('settings:label', cfg),
  // Sennik (narx yorliqlari) → TSPL bilan label printerga jim chop etish
  printLabels: (payload) => ipcRenderer.invoke('label:print', payload),
  // opts berilsa { html, silent, deviceName, widthMicrons, heightMicrons } yuboriladi
  printHtml: (html, opts) => ipcRenderer.invoke(
    'print:html', opts ? Object.assign({ html }, opts) : html),
  getClients: () => ipcRenderer.invoke('data:clients'),
  getAdmins: () => ipcRenderer.invoke('data:admins'),
  getSettings: () => ipcRenderer.invoke('data:settings'),
  saveSettings: (payload) => ipcRenderer.invoke('data:settingsSave', payload),
  // Yetkazib beruvchilar (suppliers)
  getSuppliers: () => ipcRenderer.invoke('data:suppliers'),
  supplierSave: (payload) => ipcRenderer.invoke('data:supplierSave', payload),
  supplierDelete: (id) => ipcRenderer.invoke('data:supplierDelete', { id }),
  productSetSupplier: (product_id, supplier_id) => ipcRenderer.invoke('data:productSetSupplier', { product_id, supplier_id }),
  // Mijozlar boshqaruvi
  clientSave: (payload) => ipcRenderer.invoke('data:clientSave', payload),
  clientUpdate: (payload) => ipcRenderer.invoke('data:clientUpdate', payload),
  clientDelete: (client_id) => ipcRenderer.invoke('data:clientDelete', { client_id }),
  clientPayment: (payload) => ipcRenderer.invoke('data:clientPayment', payload),
  clientBonus: (payload) => ipcRenderer.invoke('data:clientBonus', payload),
  clientDetail: (client_id) => ipcRenderer.invoke('data:clientDetail', { client_id }),
  clientsTop: (days) => ipcRenderer.invoke('data:clientsTop', { days }),
  // Xodimlar boshqaruvi + KPI
  getStaff: () => ipcRenderer.invoke('data:staff'),
  staffAdd: (payload) => ipcRenderer.invoke('data:staffAdd', payload),
  staffRole: (telegram_id, role) => ipcRenderer.invoke('data:staffRole', { telegram_id, role }),
  staffRemove: (payload) => ipcRenderer.invoke('data:staffRemove', payload),
  staffStats: (days) => ipcRenderer.invoke('data:staffStats', { days }),
  getLogo: () => ipcRenderer.invoke('assets:logo'),
  // ⌘R bosilganda main process shu hodisani yuboradi — sahifa yangilanadi.
  onRefresh: (cb) => ipcRenderer.on('app:refresh', () => cb()),
  // Avtomatik yangilanish (tugma orqali)
  getUpdate: () => ipcRenderer.invoke('update:get'),
  checkUpdate: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  onUpdateState: (cb) => ipcRenderer.on('update:state', (_e, st) => cb(st)),
});
