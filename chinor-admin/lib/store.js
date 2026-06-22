'use strict';
// Lokal ombor — sof JSON fayl (native modul yo'q, oson yig'iladi).
// Faqat sozlamalar va sessiyani saqlaydi (hisobotlar har safar serverdan olinadi).

const fs = require('fs');

// Server admin kompyuterida ishlasa shu eng ishonchli manzil. Login oynasidan
// o'zgartirsa bo'ladi (masalan, Cloudflare tunnel manzili).
const DEFAULT_SERVER = 'http://127.0.0.1:8765';

class Store {
  constructor(file) {
    this.file = file;
    this.data = this._load();
  }

  _default() {
    return {
      serverUrl: DEFAULT_SERVER,
      token: '',
      admin: null,       // {id, name}
      usdRate: 12500,
      loggedOut: false,
      // Sennik (narx yorliqlari) chop etish sozlamalari
      labelPrinter: '',  // tanlangan label printer (bo'sh = tizim oynasi)
      labelW: 30,        // yorliq eni (mm)
      labelH: 20,        // yorliq bo'yi (mm)
    };
  }

  _load() {
    try {
      const raw = fs.readFileSync(this.file, 'utf8');
      return Object.assign(this._default(), JSON.parse(raw));
    } catch {
      return this._default();
    }
  }

  save() {
    try {
      fs.writeFileSync(this.file, JSON.stringify(this.data, null, 2), 'utf8');
    } catch (e) {
      // disk to'la / ruxsat yo'q — jim o'tamiz, ilova ishlashda davom etadi
    }
  }

  get(key) { return this.data[key]; }
  set(key, val) { this.data[key] = val; this.save(); }

  setServerUrl(url) {
    this.data.serverUrl = String(url || '').trim().replace(/\/+$/, '') || DEFAULT_SERVER;
    this.save();
    return this.data.serverUrl;
  }

  setSession({ token, admin, usdRate }) {
    this.data.token = token || '';
    if (admin) this.data.admin = admin;
    if (usdRate) this.data.usdRate = usdRate;
    this.data.loggedOut = false;
    this.save();
  }

  clearSession() {
    this.data.token = '';
    this.data.loggedOut = true;
    this.save();
  }
}

module.exports = { Store, DEFAULT_SERVER };
