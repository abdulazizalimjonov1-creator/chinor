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
  getOrders: () => ipcRenderer.invoke('data:orders'),
  getProducts: (page) => ipcRenderer.invoke('data:products', page || 0),
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
  // Yetkazib beruvchilar (suppliers)
  getSuppliers: () => ipcRenderer.invoke('data:suppliers'),
  supplierSave: (payload) => ipcRenderer.invoke('data:supplierSave', payload),
  supplierDelete: (id) => ipcRenderer.invoke('data:supplierDelete', { id }),
  productSetSupplier: (product_id, supplier_id) => ipcRenderer.invoke('data:productSetSupplier', { product_id, supplier_id }),
  getLogo: () => ipcRenderer.invoke('assets:logo'),
});
