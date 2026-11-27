'use strict';
// ════════════════════════════════════════════════════════════════════════════
// 📹 Lokal kameralar — kamerani KOMPYUTERNING O'ZIDAN ochish
// ════════════════════════════════════════════════════════════════════════════
// Ilgari tasvir internetdagi server orqali kelardi (bot/api_cams.py): internet
// uzilsa — kamera ham "o'chib" qolardi, garchi kompyuter va kameralar bitta
// switch'da tursa ham. Endi tasvir to'g'ridan-to'g'ri olinadi:
//
//   kamera ──RTSP (lokal tarmoq)──► ffmpeg (shu kompyuterda) ──► 127.0.0.1
//
// Internet umuman kerak emas — faqat switch/kabel ishlasa bas.
//
// "Qotmasligi" uchun:
//   • har kamera o'z ffmpeg jarayonida — bittasi osilsa boshqalari ishlaydi;
//   • QOROVUL (watchdog): ko'rilayotgan kameradan 10 soniya kadr kelmasa
//     jarayon o'ldiriladi va qayta ulanadi (kamera o'chib-yonsa, kabel
//     chiqib-tushsa — o'zi tiklanadi, hech narsa bosish shart emas);
//   • qayta ulanish oralig'i 1 → 2 → 4 … 15 soniyagacha o'sadi, kamera
//     butunlay o'chiq bo'lsa kompyuterni band qilmaydi;
//   • hech kim ko'rmayotgan kamera 20 soniyadan keyin o'chiriladi;
//   • brauzerga MJPEG oqimi uzilmaydi: ffmpeg qayta ulanayotganda ham
//     oqim ochiq turadi, yangi kadrlar kelishi bilan davom etadi.
//
// Kameralar ro'yxati shu kompyuterda (userData/chinor-cams.json) saqlanadi,
// parollar Windows/macOS tizim kaliti bilan shifrlanadi (safeStorage).

const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const http = require('http');
const https = require('https');
const zlib = require('zlib');
const crypto = require('crypto');
const { spawn, execFile } = require('child_process');

const RTSP_PORT = 554;
const IDLE_STOP = 20000;     // hech kim ko'rmasa shuncha ms dan keyin o'chadi
const STALL = 10000; const LIVE_MS = 5000;        // kadr kelmasa shuncha ms dan keyin qayta ulanadi
const FRAME_WAIT = 12000;    // snapshot so'rovi birinchi kadrni shuncha kutadi
const MAX_CAMS = 128;

// Oqim profillari. Lokal tarmoqda trafik muammo emas — server variantidan
// ko'ra tiniqroq va silliqroq.
const PROFILES = {
  grid: { fps: 3, width: 640, q: 6, path: 'sub' },
  full: { fps: 12, width: 1280, q: 4, path: 'main' },
  hi:   { fps: 8, width: 1920, q: 3, path: 'main' },
};

// Brendlar bo'yicha RTSP yo'llari. {ch} — NVR/DVR kanal raqami (oddiy
// kamerada 1). Avtomatik aniqlashda navbat bilan sinaladi.
const PATH_TEMPLATES = [
  ['/Streaming/Channels/{ch}01', '/Streaming/Channels/{ch}02'],        // Hikvision, HiWatch
  ['/cam/realmonitor?channel={ch}&subtype=0',
   '/cam/realmonitor?channel={ch}&subtype=1'],                         // Dahua, Imou
  ['/h264/ch{ch}/main/av_stream', '/h264/ch{ch}/sub/av_stream'],      // Hikvision eski
  ['/profile1', '/profile2'],                                         // TVT / ONVIF
  ['/h264Preview_0{ch}_main', '/h264Preview_0{ch}_sub'],              // Reolink
  ['/user=admin_password=_channel={ch}_stream=0.sdp',
   '/user=admin_password=_channel={ch}_stream=1.sdp'],                 // XMeye (eski)
  ['/live/ch0{ch}', '/live/ch1'],
  ['/onvif1', '/onvif2'],
  ['/stream1', '/stream2'],
  ['/11', '/12'],                                                     // XM / Jovision
  ['/live0.264', '/live1.264'],
  ['/ch0{ch}/0', '/ch0{ch}/1'],
  ['/live', ''],
  ['/', ''],
];

const SOI = Buffer.from([0xff, 0xd8]);
const EOI = Buffer.from([0xff, 0xd9]);

function maskUrl(s) {
  return String(s || '').replace(/rtsp:\/\/[^:/@\s]+:[^@\s]+@/gi, 'rtsp://***:***@');
}

// ─── ffmpeg ni topish ───────────────────────────────────────────────────────
const EXE = process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg';

function tryFfmpeg(bin) {
  return new Promise((resolve) => {
    if (!bin) return resolve(false);
    if (path.isAbsolute(bin) && !fs.existsSync(bin)) return resolve(false);
    execFile(bin, ['-hide_banner', '-version'], { timeout: 8000, windowsHide: true },
      (err, out) => resolve(!err && /ffmpeg version/i.test(String(out || ''))));
  });
}

// ─── Asosiy dvigatel ────────────────────────────────────────────────────────
class CamEngine {
  /**
   * @param {object} o
   * @param {string} o.dataDir   — userData papkasi (ro'yxat va yuklab olingan ffmpeg)
   * @param {string} [o.resourcesPath] — o'rnatilgan ilova resurslari (ffmpeg shu yerda)
   * @param {string} [o.appDir]  — ilova papkasi (dev rejimda vendor/ffmpeg)
   * @param {object} [o.safeStorage] — electron.safeStorage (parol shifrlash)
   */
  constructor(o) {
    this.dataDir = o.dataDir;
    this.resourcesPath = o.resourcesPath || '';
    this.appDir = o.appDir || '';
    this.safe = o.safeStorage || null;
    this.file = path.join(this.dataDir, 'chinor-cams.json');
    this.data = this._load();
    this.workers = new Map();       // "id:profile" → Worker
    this.audio = new Map();         // id → AudioWorker
    this.token = crypto.randomBytes(18).toString('hex');
    this.port = 0;
    this.server = null;
    this.ff = '';                   // topilgan ffmpeg yo'li
    this._ffChecked = false;
    this._reaper = setInterval(() => this._reap(), 4000);
    if (this._reaper.unref) this._reaper.unref();
  }

  // ── Saqlash ──
  _load() {
    try {
      const d = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      if (!Array.isArray(d.cams)) d.cams = [];
      d.seq = Number(d.seq) || d.cams.reduce((m, c) => Math.max(m, c.id || 0), 0);
      return d;
    } catch {
      return { seq: 0, cams: [], ffmpegPath: '' };
    }
  }

  _save() {
    try {
      fs.mkdirSync(this.dataDir, { recursive: true });
      const tmp = this.file + '.tmp';
      fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2), 'utf8');
      fs.renameSync(tmp, this.file);
    } catch (_) { /* disk to'la — jim */ }
  }

  _enc(pw) {
    pw = String(pw || '');
    if (!pw) return '';
    try {
      if (this.safe && this.safe.isEncryptionAvailable()) {
        return 'enc:' + this.safe.encryptString(pw).toString('base64');
      }
    } catch (_) {}
    return 'b64:' + Buffer.from(pw, 'utf8').toString('base64');
  }

  _dec(v) {
    v = String(v || '');
    try {
      if (v.startsWith('enc:') && this.safe) return this.safe.decryptString(Buffer.from(v.slice(4), 'base64'));
      if (v.startsWith('b64:')) return Buffer.from(v.slice(4), 'base64').toString('utf8');
    } catch (_) { return ''; }
    return v;
  }

  _pub(c) {
    // Renderer'ga PAROL berilmaydi — faqat bor-yo'qligi.
    const st = this.status(c.id);
    return {
      id: c.id, name: c.name, host: c.host, port: c.port || RTSP_PORT,
      username: c.username || '', has_password: !!c.password,
      channel: c.channel || 1, main_path: c.main_path || '', sub_path: c.sub_path || '',
      url: c.url || '', enabled: c.enabled !== false, sort: c.sort || 0,
      ready: !!(c.url || c.main_path || c.sub_path),
      live: st.live, error: st.error,
    };
  }

  list() {
    return this.data.cams.slice().sort((a, b) => (a.sort || 0) - (b.sort || 0) || a.id - b.id)
      .map((c) => this._pub(c));
  }

  _cam(id) { return this.data.cams.find((c) => c.id === Number(id)) || null; }

  save(body) {
    body = body || {};
    const id = Number(body.id) || 0;
    const host = String(body.host || '').trim();
    const url = String(body.url || '').trim();
    if (!host && !url) return { ok: false, error: 'Kamera IP manzilini kiriting' };
    if (host && !/^[A-Za-z0-9.\-]+$/.test(host)) return { ok: false, error: 'IP manzil noto\'g\'ri' };
    if (url && !/^(rtsp|rtsps|http|https):\/\//i.test(url)) return { ok: false, error: 'Manzil rtsp:// bilan boshlansin' };
    const keep = (k, prev) => (body[k] === undefined || body[k] === null) ? (prev[k] || '') : String(body[k]).trim();
    let c = id ? this._cam(id) : null;
    if (id && !c) return { ok: false, error: 'Kamera topilmadi' };
    if (!c) {
      if (this.data.cams.length >= MAX_CAMS) return { ok: false, error: `Kameralar ${MAX_CAMS} tadan oshmasin` };
      c = { id: ++this.data.seq, created_at: new Date().toISOString() };
      this.data.cams.push(c);
    }
    const prev = Object.assign({}, c);
    c.name = String(body.name || '').trim() || (host ? `Kamera ${host.split('.').pop()}` : `Kamera ${c.id}`);
    c.host = host || (url.match(/^[a-z]+:\/\/(?:[^@/]*@)?([^:/?]+)/i) || [])[1] || '';
    c.port = Number(body.port) || RTSP_PORT;
    c.channel = Math.max(1, Math.min(64, Number(body.channel) || prev.channel || 1));
    c.username = keep('username', prev);
    // Parol: bo'sh kelsa — eskisi qoladi (forma parolni ko'rsatmaydi).
    if (body.password !== undefined && body.password !== null && String(body.password) !== '') {
      c.password = this._enc(body.password);
    } else if (body.clear_password) {
      c.password = '';
    }
    c.main_path = keep('main_path', prev);
    c.sub_path = keep('sub_path', prev);
    c.url = url;
    c.enabled = body.enabled !== false;
    c.sort = Number(body.sort) || prev.sort || c.id;
    this._save();
    this._restart(c.id);
    return { ok: true, cam: this._pub(c) };
  }

  remove(id) {
    id = Number(id);
    this._restart(id);
    this.data.cams = this.data.cams.filter((c) => c.id !== id);
    this._save();
    return { ok: true };
  }

  reorder(ids) {
    (ids || []).forEach((id, i) => { const c = this._cam(id); if (c) c.sort = i + 1; });
    this._save();
    return { ok: true };
  }

  /** Serverdagi (Mini App'da qo'shilgan) kameralarni shu kompyuterga ko'chirish. */
  importFrom(list, sharedPassword) {
    let added = 0, skipped = 0;
    for (const s of list || []) {
      const host = String(s.host || '').trim();
      if (!host) continue;
      const dup = this.data.cams.find((c) => c.host === host &&
        (c.main_path || '') === (s.main_path || '') && (c.port || RTSP_PORT) === (Number(s.port) || RTSP_PORT));
      if (dup) { skipped++; continue; }
      const pw = s.password != null && s.password !== '' ? s.password : (sharedPassword || '');
      const r = this.save({
        name: s.name, host, port: s.port, username: s.username || '',
        password: pw, main_path: s.main_path || '', sub_path: s.sub_path || '',
        enabled: s.enabled !== false,
      });
      if (r.ok) added++;
    }
    return { ok: true, added, skipped };
  }

  rtspUrl(c, profile) {
    if (c.url) return c.url;
    const want = (PROFILES[profile] || PROFILES.grid).path === 'sub';
    let p = (want ? c.sub_path : '') || c.main_path || c.sub_path || '';
    p = String(p).replace(/\{ch\}/g, String(c.channel || 1));
    if (p && !p.startsWith('/')) p = '/' + p;
    let auth = '';
    const user = String(c.username || '').trim();
    if (user) auth = encodeURIComponent(user) + ':' + encodeURIComponent(this._dec(c.password)) + '@';
    return `rtsp://${auth}${c.host}:${c.port || RTSP_PORT}${p}`;
  }

  // ── ffmpeg ──
  _ffCandidates() {
    const plat = `${process.platform}-${process.arch}`;
    const out = [];
    if (this.data.ffmpegPath) out.push(this.data.ffmpegPath);
    if (this.resourcesPath) out.push(path.join(this.resourcesPath, 'ffmpeg', EXE));
    out.push(path.join(this.dataDir, 'ffmpeg', EXE));
    if (this.appDir) out.push(path.join(this.appDir, 'vendor', 'ffmpeg', plat, EXE));
    if (process.platform === 'win32') {
      const pf = process.env.ProgramFiles || 'C:\\Program Files';
      out.push('C:\\ffmpeg\\bin\\ffmpeg.exe', path.join(pf, 'ffmpeg', 'bin', 'ffmpeg.exe'));
      if (process.env.LOCALAPPDATA) {
        out.push(path.join(process.env.LOCALAPPDATA, 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe'));
      }
    } else {
      out.push('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/usr/bin/ffmpeg');
    }
    out.push('ffmpeg');            // PATH
    return out;
  }

  async ffmpeg(force) {
    if (this.ff && !force) return this.ff;
    if (this._ffChecked && !force) return '';
    for (const c of this._ffCandidates()) {
      if (await tryFfmpeg(c)) { this.ff = c; this._ffChecked = true; return c; }
    }
    this._ffChecked = true;
    this.ff = '';
    return '';
  }

  setFfmpegPath(p) {
    this.data.ffmpegPath = String(p || '');
    this._save();
    this.ff = '';
    this._ffChecked = false;
    return this.ffmpeg(true);
  }

  /** ffmpeg yo'q bo'lsa — bir martalik yuklab olish (internet kerak, keyin kerak emas). */
  async downloadFfmpeg(onProgress) {
    const map = { 'win32-x64': 'win32-x64', 'win32-ia32': 'win32-ia32', 'darwin-arm64': 'darwin-arm64',
                  'darwin-x64': 'darwin-x64', 'linux-x64': 'linux-x64', 'linux-arm64': 'linux-arm64' };
    const key = map[`${process.platform}-${process.arch}`];
    if (!key) return { ok: false, error: 'Bu tizim uchun tayyor ffmpeg yo\'q' };
    const url = `https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0/ffmpeg-${key}.gz`;
    const dir = path.join(this.dataDir, 'ffmpeg');
    const dest = path.join(dir, EXE);
    fs.mkdirSync(dir, { recursive: true });
    try {
      await new Promise((resolve, reject) => {
        const get = (u, hops) => {
          https.get(u, { headers: { 'User-Agent': 'chinor-admin' } }, (res) => {
            if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location && hops < 6) {
              res.resume();
              return get(new URL(res.headers.location, u).toString(), hops + 1);
            }
            if (res.statusCode !== 200) { res.resume(); return reject(new Error('HTTP ' + res.statusCode)); }
            const total = Number(res.headers['content-length']) || 0;
            let got = 0;
            res.on('data', (ch) => { got += ch.length; if (onProgress && total) onProgress(Math.round(got * 100 / total)); });
            const tmp = dest + '.part';
            const out = fs.createWriteStream(tmp, { mode: 0o755 });
            res.pipe(zlib.createGunzip()).on('error', reject).pipe(out);
            out.on('finish', () => { try { fs.renameSync(tmp, dest); resolve(); } catch (e) { reject(e); } });
            out.on('error', reject);
          }).on('error', reject);
        };
        get(url, 0);
      });
      try { fs.chmodSync(dest, 0o755); } catch (_) {}
    } catch (e) {
      return { ok: false, error: 'Yuklab bo\'lmadi: ' + e.message };
    }
    const ff = await this.ffmpeg(true);
    return ff ? { ok: true, path: ff } : { ok: false, error: 'ffmpeg ishga tushmadi' };
  }

  // ── Ishchilar ──
  _restart(id) {
    for (const [k, w] of this.workers) {
      if (k.startsWith(id + ':')) { w.destroy(); this.workers.delete(k); }
    }
    const a = this.audio.get(String(id));
    if (a) { a.destroy(); this.audio.delete(String(id)); }
  }

  _reap() {
    const now = Date.now();
    for (const [k, w] of this.workers) {
      if (!w.subs.size && now - w.touched > IDLE_STOP) { w.destroy(); this.workers.delete(k); }
    }
    for (const [k, a] of this.audio) {
      if (!a.subs.size && now - a.touched > 8000) { a.destroy(); this.audio.delete(k); }
    }
  }

  _worker(c, profile) {
    const key = `${c.id}:${profile}`;
    let w = this.workers.get(key);
    if (!w) {
      w = new Worker(this, c, profile);
      this.workers.set(key, w);
    }
    w.touch();
    return w;
  }

  status(id) {
    let live = false, error = '', at = 0;
    for (const [k, w] of this.workers) {
      if (!k.startsWith(id + ':')) continue;
      if (w.frameAt > at) at = w.frameAt;
      if (Date.now() - w.frameAt < LIVE_MS) live = true;
      if (w.error) error = w.error;
    }
    return { live, error: live ? '' : error, at };
  }

  statusAll() {
    const out = {};
    for (const c of this.data.cams) out[c.id] = this.status(c.id);
    return out;
  }

  stopAll() {
    for (const w of this.workers.values()) w.destroy();
    for (const a of this.audio.values()) a.destroy();
    this.workers.clear();
    this.audio.clear();
  }

  // ── Lokal HTTP server (faqat 127.0.0.1) ──
  start() {
    if (this.server) return Promise.resolve(this.port);
    return new Promise((resolve, reject) => {
      const srv = http.createServer((req, res) => this._handle(req, res));
      srv.on('error', reject);
      srv.listen(0, '127.0.0.1', () => {
        this.server = srv;
        this.port = srv.address().port;
        resolve(this.port);
      });
    });
  }

  base() { return `http://127.0.0.1:${this.port}`; }

  async _handle(req, res) {
    const u = new URL(req.url, 'http://x');
    const fail = (code, msg) => {
      if (res.headersSent) { try { res.end(); } catch (_) {} return; }
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify({ ok: false, error: msg }));
    };
    if (u.searchParams.get('t') !== this.token) return fail(401, 'token');
    const m = u.pathname.match(/^\/cam\/(\d+)\/(snap\.jpg|live\.mjpg|audio)$/);
    if (!m) return fail(404, 'yo\'q');
    const c = this._cam(m[1]);
    if (!c || c.enabled === false) return fail(404, 'kamera topilmadi');
    if (!(c.url || c.main_path || c.sub_path)) return fail(404, 'kamera sozlanmagan');
    if (!(await this.ffmpeg())) return fail(503, 'ffmpeg topilmadi');
    let profile = u.searchParams.get('p') || 'grid';
    if (!PROFILES[profile]) profile = 'grid';

    if (m[2] === 'audio') return this._serveAudio(c, req, res, fail);
    const w = this._worker(c, profile);
    if (m[2] === 'snap.jpg') {
      const f = await w.waitFrame(FRAME_WAIT);
      if (!f) return fail(503, maskUrl(w.error || 'tasvir yo\'q'));
      res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'no-store' });
      return res.end(f);
    }
    // MJPEG — ffmpeg qayta ulanayotganda ham oqim ochiq turadi.
    const B = 'chinorcam';
    res.writeHead(200, {
      'Content-Type': `multipart/x-mixed-replace; boundary=${B}`,
      'Cache-Control': 'no-store', Connection: 'close',
    });
    if (res.socket) res.socket.setNoDelay(true);
    const send = (f) => {
      // Brauzer sekin o'qisa — kadrni tashlab yuboramiz (kechikish yig'ilmasin).
      if (res.writableLength > 2 * 1024 * 1024) return;
      res.write(`--${B}\r\nContent-Type: image/jpeg\r\nContent-Length: ${f.length}\r\n\r\n`);
      res.write(f);
      res.write('\r\n');
    };
    if (w.frame && Date.now() - w.frameAt < STALL) send(w.frame);
    w.subs.add(send);
    const done = () => { w.subs.delete(send); w.touch(); };
    req.on('close', done);
    res.on('error', done);
  }

  _serveAudio(c, req, res, fail) {
    const key = String(c.id);
    let a = this.audio.get(key);
    if (!a) { a = new AudioWorker(this, c); this.audio.set(key, a); }
    a.touch();
    a.waitStart(12000).then((first) => {
      if (!first) return fail(503, maskUrl(a.error || 'ovoz yo\'q'));
      res.writeHead(200, { 'Content-Type': 'audio/aac', 'Cache-Control': 'no-store', Connection: 'close' });
      const send = (ch) => { if (res.writableLength < 512 * 1024) res.write(ch); };
      a.subs.add(send);
      const done = () => { a.subs.delete(send); a.touch(); };
      req.on('close', done);
      res.on('error', done);
    });
  }

  // ── Tarmoqdan izlash ──
  subnets() {
    const out = [];
    const ifs = os.networkInterfaces();
    for (const name of Object.keys(ifs)) {
      for (const a of ifs[name] || []) {
        if (a.family !== 'IPv4' && a.family !== 4) continue;
        if (a.internal || a.address.startsWith('169.254.')) continue;
        const pre = a.address.split('.').slice(0, 3).join('.');
        if (!out.find((x) => x.prefix === pre)) out.push({ prefix: pre, ip: a.address, iface: name });
      }
    }
    return out;
  }

  async discover(subnet) {
    let prefixes = [];
    if (subnet) prefixes = [String(subnet).trim().replace(/\.+$/, '').split('.').slice(0, 3).join('.')];
    else prefixes = this.subnets().map((s) => s.prefix);
    prefixes = prefixes.filter((p) => /^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(p));
    const mine = new Set(this.subnets().map((s) => s.ip));
    const have = new Set(this.data.cams.map((c) => c.host));
    const hosts = [];
    for (const p of prefixes) for (let i = 1; i < 255; i++) hosts.push(`${p}.${i}`);
    const found = [];
    let idx = 0;
    const one = async () => {
      while (idx < hosts.length) {
        const h = hosts[idx++];
        if (mine.has(h)) continue;
        if (await portOpen(h, RTSP_PORT, 700)) found.push(h);
      }
    };
    await Promise.all(Array.from({ length: 96 }, one));
    found.sort((a, b) => a.split('.').map(Number).reduce((x, y) => x * 256 + y) -
                         b.split('.').map(Number).reduce((x, y) => x * 256 + y));
    return { ok: true, subnets: prefixes, found: found.map((h) => ({ host: h, added: have.has(h) })) };
  }

  /** RTSP yo'lini avtomatik aniqlash. body: {id?|host, port, username, password, channel} */
  async probe(body) {
    const ff = await this.ffmpeg();
    if (!ff) return { ok: false, error: 'ffmpeg topilmadi' };
    let cam;
    if (body.id) {
      const saved = this._cam(body.id);
      if (!saved) return { ok: false, error: 'Kamera topilmadi' };
      cam = Object.assign({}, saved);
    } else {
      cam = { host: String(body.host || '').trim(), port: Number(body.port) || RTSP_PORT };
    }
    if (body.host) cam.host = String(body.host).trim();
    if (body.port) cam.port = Number(body.port);
    if (body.username !== undefined) cam.username = String(body.username || '').trim();
    if (body.password) cam.password = this._enc(body.password);
    if (body.channel) cam.channel = Number(body.channel) || 1;
    cam.url = String(body.url || '').trim();
    if (!cam.host && !cam.url) return { ok: false, error: 'IP manzil yo\'q' };

    if (cam.url) {
      const r = await tryUrl(ff, cam.url, 8000);
      return r.ok ? { ok: true, url: cam.url, jpeg: r.jpeg.toString('base64') }
                  : { ok: false, error: maskUrl(r.error) };
    }
    if (!(await portOpen(cam.host, cam.port || RTSP_PORT, 2500))) {
      return { ok: false, error: `${cam.host}:${cam.port || RTSP_PORT} javob bermayapti — IP yoki kabelni tekshiring` };
    }
    // Yo'l qo'lda yozilgan — faqat shuni tekshiramiz.
    const qolda = String(body.main_path || '').trim();
    const shablon = qolda ? [[qolda, String(body.sub_path || '').trim()]] : PATH_TEMPLATES;
    let last = '', authFail = false;
    for (const [main, sub] of shablon) {
      const t = Object.assign({}, cam, { main_path: main, sub_path: '', url: '' });
      const r = await tryUrl(ff, this.rtspUrl(t, 'full'), 7000);
      if (!r.ok) {
        // 401 da boshqa yo'llarni sinamaymiz: parol baribir bir xil, har xato
        // urinish esa Hikvision'ni ~30 daqiqaga bloklaydi (to'g'ri parolga ham 401).
        if (/parol|\b401\b|Unauthorized/i.test(r.error)) { authFail = true; break; }
        last = r.error || last;
        continue;
      }
      let subOk = false;
      if (sub) {
        t.sub_path = sub;
        subOk = (await tryUrl(ff, this.rtspUrl(t, 'grid'), 7000)).ok;
      }
      return { ok: true, main_path: main, sub_path: subOk ? sub : '', jpeg: r.jpeg.toString('base64') };
    }
    if (authFail) return { ok: false, error: 'Login yoki parol noto\'g\'ri. Parol to\'g\'ri bo\'lsa — kamera ko\'p xato urinishdan keyin ~30 daqiqaga bloklangan, keyin bir marta qayta urinib ko\'ring' };
    return { ok: false, error: maskUrl(last || 'kamera javob bermadi') };
  }
}

// ─── ffmpeg ishchisi: RTSP → JPEG kadrlar (o'zi qayta ulanadi) ──────────────
class Worker {
  constructor(eng, cam, profile) {
    this.eng = eng;
    this.cam = cam;
    this.profile = profile;
    this.prof = PROFILES[profile] || PROFILES.grid;
    this.proc = null;
    this.frame = null;
    this.frameAt = 0;
    this.startedAt = 0;
    this.touched = Date.now();
    this.error = '';
    this.subs = new Set();
    this.waiters = [];
    this.backoff = 1000;
    this.dead = false;
    this.pending = false;
    this.retryT = null;
    this.dog = setInterval(() => this._watch(), 2000);
    this._spawn();
  }

  touch() {
    this.touched = Date.now();
    if (!this.dead && !this.proc && !this.pending) this._spawn();
  }

  _watch() {
    if (this.dead || !this.proc) return;
    const ref = Math.max(this.frameAt, this.startedAt);
    // Kadr kelmay qoldi — ffmpeg osilib qolgan (kamera o'chgan, kabel chiqqan).
    if (Date.now() - ref > STALL + (this.frameAt ? 0 : 5000)) {
      this.error = this.error || 'kameradan tasvir kelmayapti — qayta ulanmoqda';
      try { this.proc.kill('SIGKILL'); } catch (_) {}
    }
  }

  _args(url) {
    const p = this.prof;
    const a = ['-hide_banner', '-loglevel', 'error', '-nostdin'];
    if (/^rtsps?:/i.test(url)) {
      a.push('-rtsp_transport', 'tcp', '-timeout', '6000000');
    } else if (/^https?:/i.test(url)) {
      a.push('-rw_timeout', '6000000', '-reconnect', '1', '-reconnect_streamed', '1');
    }
    a.push('-fflags', 'nobuffer+discardcorrupt', '-flags', 'low_delay',
           '-analyzeduration', '1000000', '-probesize', '1000000',
           '-i', url, '-an', '-sn', '-dn',
           '-vf', `fps=${p.fps},scale='min(${p.width},iw)':-2`,
           '-q:v', String(p.q), '-f', 'mjpeg', 'pipe:1');
    return a;
  }

  _spawn() {
    if (this.dead) return;
    const ff = this.eng.ff;
    if (!ff) { this.error = 'ffmpeg topilmadi'; return this._retry(); }
    const url = this.eng.rtspUrl(this.cam, this.profile);
    let proc;
    try {
      proc = spawn(ff, this._args(url), { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (e) {
      this.error = 'ffmpeg ishga tushmadi: ' + e.message;
      return this._retry();
    }
    this.proc = proc;
    this.startedAt = Date.now();
    let buf = Buffer.alloc(0);
    proc.stdout.on('data', (ch) => {
      buf = buf.length ? Buffer.concat([buf, ch]) : ch;
      for (;;) {
        const s = buf.indexOf(SOI);
        if (s < 0) { buf = Buffer.alloc(0); break; }
        const e = buf.indexOf(EOI, s + 2);
        if (e < 0) { if (s > 0) buf = buf.subarray(s); break; }
        this._emit(Buffer.from(buf.subarray(s, e + 2)));
        buf = buf.subarray(e + 2);
      }
      if (buf.length > 8 * 1024 * 1024) buf = Buffer.alloc(0);
    });
    proc.stderr.on('data', (d) => {
      const t = String(d).trim().split(/\r?\n/).pop();
      if (t) this.error = maskUrl(humanErr(t)).slice(0, 200);
    });
    proc.on('error', (e) => { this.error = 'ffmpeg: ' + e.message; });
    proc.on('close', () => {
      if (this.proc === proc) this.proc = null;
      if (!this.dead) this._retry();
    });
  }

  _retry() {
    if (this.dead) return;
    clearTimeout(this.retryT);
    // Kimdir ko'rib turgan bo'lsa — qayta ulanamiz; aks holda reaper o'chiradi.
    let wait = this.frameAt && Date.now() - this.frameAt < 30000 ? 1000 : this.backoff;
    // Parol xato: tez-tez urinish kamerani bloklab qo'yadi — 5 daqiqada bir.
    if (/parol/.test(this.error)) wait = 5 * 60 * 1000;
    this.backoff = Math.min(15000, this.backoff * 2);
    this.pending = true;
    this.retryT = setTimeout(() => {
      this.pending = false;
      if (this.dead) return;
      // Hech kim ko'rmayapti — "uxlaymiz"; keyingi so'rov (touch) uyg'otadi.
      if (!this.subs.size && Date.now() - this.touched > IDLE_STOP) return;
      this._spawn();
    }, wait);
  }

  _emit(jpeg) {
    this.frame = jpeg;
    this.frameAt = Date.now();
    this.error = '';
    this.backoff = 1000;
    for (const send of this.subs) { try { send(jpeg); } catch (_) {} }
    const ws = this.waiters; this.waiters = [];
    ws.forEach((fn) => fn(jpeg));
  }

  waitFrame(ms) {
    this.touch();
    if (this.frame && Date.now() - this.frameAt < STALL) return Promise.resolve(this.frame);
    return new Promise((resolve) => {
      const t = setTimeout(() => {
        this.waiters = this.waiters.filter((f) => f !== fn);
        resolve(null);
      }, ms);
      const fn = (f) => { clearTimeout(t); resolve(f); };
      this.waiters.push(fn);
    });
  }

  destroy() {
    this.dead = true;
    clearInterval(this.dog);
    clearTimeout(this.retryT);
    if (this.proc) { try { this.proc.kill('SIGKILL'); } catch (_) {} }
    this.proc = null;
    this.waiters.forEach((fn) => fn(null));
    this.waiters = [];
  }
}

// ─── Ovoz: RTSP → AAC (ADTS) ────────────────────────────────────────────────
class AudioWorker {
  constructor(eng, cam) {
    this.eng = eng;
    this.cam = cam;
    this.subs = new Set();
    this.touched = Date.now();
    this.error = '';
    this.first = null;
    this.waiters = [];
    this.dead = false;
    this.proc = null;
    this._spawn();
  }

  touch() { this.touched = Date.now(); }

  _spawn() {
    const ff = this.eng.ff;
    if (!ff) { this.error = 'ffmpeg topilmadi'; return this._flush(); }
    const url = this.eng.rtspUrl(this.cam, 'full');
    const a = ['-hide_banner', '-loglevel', 'error', '-nostdin'];
    if (/^rtsps?:/i.test(url)) a.push('-rtsp_transport', 'tcp', '-timeout', '6000000');
    a.push('-fflags', 'nobuffer', '-i', url, '-vn', '-ac', '1', '-ar', '22050',
           '-c:a', 'aac', '-b:a', '48k', '-f', 'adts', '-flush_packets', '1', 'pipe:1');
    const proc = spawn(ff, a, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    this.proc = proc;
    proc.stdout.on('data', (ch) => {
      if (!this.first) { this.first = ch; this._flush(); }
      for (const s of this.subs) { try { s(ch); } catch (_) {} }
    });
    proc.stderr.on('data', (d) => {
      const t = String(d).trim();
      if (/does not contain any stream|matches no streams/i.test(t)) this.error = 'bu kamerada ovoz yo\'q';
      else if (t) this.error = maskUrl(humanErr(t.split(/\r?\n/).pop())).slice(0, 200);
    });
    proc.on('error', (e) => { this.error = e.message; this._flush(); });
    proc.on('close', () => {
      this.proc = null;
      this._flush();
      // Ovoz uzilsa (kamera qayta yuklandi) — tinglayotgan bo'lsa qayta ulanamiz.
      if (!this.dead && this.first && this.subs.size) setTimeout(() => !this.dead && this._spawn(), 1500);
    });
    setTimeout(() => { if (!this.first) this._flush(); }, 12000);
  }

  _flush() { const ws = this.waiters; this.waiters = []; ws.forEach((fn) => fn(this.first)); }

  waitStart(ms) {
    if (this.first) return Promise.resolve(this.first);
    if (!this.proc && this.error) return Promise.resolve(null);
    return new Promise((resolve) => {
      const t = setTimeout(() => resolve(this.first), ms);
      this.waiters.push((f) => { clearTimeout(t); resolve(f); });
    });
  }

  destroy() {
    this.dead = true;
    if (this.proc) { try { this.proc.kill('SIGKILL'); } catch (_) {} }
    this._flush();
  }
}

// ─── Yordamchilar ───────────────────────────────────────────────────────────
function humanErr(t) {
  // "[h264 @ 0x55b2ded0c680] ..." — manzildagi raqamlar 401/404 deb o'qilmasin
  t = String(t || '').replace(/\[[^\]]*@\s*0x[0-9a-f]+\]\s*/gi, '').trim();
  if (/\b401\b|Unauthorized/i.test(t)) return 'login yoki parol noto\'g\'ri';
  if (/\b404\b|Not Found/i.test(t)) return 'oqim yo\'li noto\'g\'ri (404)';
  if (/Connection refused/i.test(t)) return 'kamera ulanishni rad etdi';
  if (/End of file|Stream ends prematurely|Connection reset|Broken pipe|Invalid data found/i.test(t)) return 'kamera bilan aloqa uzildi';
  if (/timed out|Connection timed out|No route to host|Network is unreachable/i.test(t)) return 'kamera javob bermayapti (tarmoq)';
  return t;
}

function portOpen(host, port, ms) {
  return new Promise((resolve) => {
    const s = new net.Socket();
    let done = false;
    const fin = (v) => { if (done) return; done = true; s.destroy(); resolve(v); };
    s.setTimeout(ms);
    s.once('connect', () => fin(true));
    s.once('timeout', () => fin(false));
    s.once('error', () => fin(false));
    s.connect(port, host);
  });
}

function tryUrl(ff, url, ms) {
  return new Promise((resolve) => {
    const a = ['-hide_banner', '-loglevel', 'error', '-nostdin'];
    if (/^rtsps?:/i.test(url)) a.push('-rtsp_transport', 'tcp', '-timeout', String(ms * 1000));
    a.push('-i', url, '-frames:v', '1', '-vf', "scale='min(480,iw)':-2", '-f', 'image2', '-c:v', 'mjpeg', '-q:v', '7', 'pipe:1');
    let out = [], err = '';
    let p;
    try { p = spawn(ff, a, { windowsHide: true }); } catch (e) { return resolve({ ok: false, error: e.message }); }
    const t = setTimeout(() => { try { p.kill('SIGKILL'); } catch (_) {} }, ms + 4000);
    p.stdout.on('data', (d) => out.push(d));
    p.stderr.on('data', (d) => { err += d; });
    p.on('error', (e) => { err = e.message; });
    p.on('close', () => {
      clearTimeout(t);
      const b = Buffer.concat(out);
      if (b.length > 2 && b[0] === 0xff && b[1] === 0xd8) return resolve({ ok: true, jpeg: b });
      const last = err.trim().split(/\r?\n/).pop() || 'tasvir kelmadi (vaqt tugadi)';
      resolve({ ok: false, error: humanErr(last).slice(0, 180) });
    });
  });
}

module.exports = { CamEngine, PROFILES, PATH_TEMPLATES, maskUrl };
