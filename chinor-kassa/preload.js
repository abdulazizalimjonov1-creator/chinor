'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// Renderer (UI) faqat shu xavfsiz API orqali main process bilan gaplashadi.
contextBridge.exposeInMainWorld('kassa', {
  getState: () => ipcRenderer.invoke('state:get'),
  login: (login, password) => ipcRenderer.invoke('auth:login', { login, password }),
  logout: () => ipcRenderer.invoke('auth:logout'),
  setServerUrl: (url) => ipcRenderer.invoke('settings:server', url),
  setKassaNo: (n) => ipcRenderer.invoke('settings:kassa', n),
  getCatalog: () => ipcRenderer.invoke('catalog:get'),
  setProductBarcode: (productId, barcode) => ipcRenderer.invoke('product:setBarcode', { productId, barcode }),
  getClients: () => ipcRenderer.invoke('clients:get'),
  recordSale: (sale) => ipcRenderer.invoke('sale:record', sale),
  recordReturn: (ret) => ipcRenderer.invoke('sale:return', ret),
  // Smena (shift)
  getShift: () => ipcRenderer.invoke('shift:get'),
  openShift: (opts) => ipcRenderer.invoke('shift:open', opts),
  closeShift: () => ipcRenderer.invoke('shift:close'),
  shiftSummary: () => ipcRenderer.invoke('shift:summary'),
  addCashMove: (mv) => ipcRenderer.invoke('shift:cashmove', mv),
  // Kassirlar (lokal) + kassa qulfi (Win-lock / topshirish)
  listCashiers: () => ipcRenderer.invoke('cashiers:list'),
  addCashier: (name, pin, authPin) => ipcRenderer.invoke('cashier:add', { name, pin, authPin }),
  removeCashier: (id, authPin) => ipcRenderer.invoke('cashier:remove', { id, authPin }),
  lockKassa: (counted) => ipcRenderer.invoke('kassa:lock', { counted }),
  unlockKassa: (pin) => ipcRenderer.invoke('kassa:unlock', { pin }),
  openShiftManual: (pin, openCash) => ipcRenderer.invoke('shift:openManual', { pin, openCash }),
  handoverKassa: (pin, counted) => ipcRenderer.invoke('kassa:handover', { pin, counted }),
  syncNow: () => ipcRenderer.invoke('sync:now'),
  getTodaySales: () => ipcRenderer.invoke('sales:today'),
  getRecentSales: () => ipcRenderer.invoke('sales:recent'),
  resyncReceipts: () => ipcRenderer.invoke('sales:resync'),
  getDrafts: () => ipcRenderer.invoke('drafts:get'),
  saveDraft: (draft) => ipcRenderer.invoke('drafts:save', draft),
  removeDraft: (id) => ipcRenderer.invoke('drafts:remove', id),
  getTabs: () => ipcRenderer.invoke('tabs:get'),
  saveTabs: (list) => ipcRenderer.invoke('tabs:set', list),
  printReceipt: (html) => ipcRenderer.invoke('receipt:print', html),
  // macOS: termal printer drayverisiz — chek "model"ini ESC/POS qilib bosamiz
  isMac: process.platform === 'darwin',
  printReceiptMac: (model) => ipcRenderer.invoke('receipt:printmac', model),
  getLogo: () => ipcRenderer.invoke('assets:logo'),
  getPrinters: () => ipcRenderer.invoke('printers:list'),
  setPrinter: (name) => ipcRenderer.invoke('printer:set', name),
  checkUpdate: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  // Telegram bot orqali to'lov linkini olish va xabar yuborish
  beginPayment: () => ipcRenderer.invoke('payment:begin'),
  pollPaymentLink: () => ipcRenderer.invoke('payment:poll'),
  sendPaymentMsg: (text) => ipcRenderer.invoke('payment:sendmsg', text),
  updateSaleQr: (local_id, qrLink) => ipcRenderer.invoke('sale:updateQr', { local_id, qrLink }),
  updateSaleQrs: (local_id, qrLinks) => ipcRenderer.invoke('sale:updateQr', { local_id, qrLinks }),
  // Holat o'zgarganda (online/offline, sinxron) UI ni yangilash uchun
  onState: (cb) => {
    const handler = (_e, state) => cb(state);
    ipcRenderer.on('state:update', handler);
    return () => ipcRenderer.removeListener('state:update', handler);
  },
});
