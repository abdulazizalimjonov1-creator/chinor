"""Kameralar — do'kondagi IP kameralarni Mini App'dan ko'rish.

Kameralar do'kon tarmog'ida (192.168.x.x) turadi va tashqaridan
ko'rinmaydi. Server esa O'SHA tarmoqda, ethernet bilan ulangan — demak
u kameraga yeta oladi. Shuning uchun oqimni SERVER oladi va Mini App'ga
o'zining HTTPS manzili orqali uzatadi: telefonda hech qanday port
ochish, router sozlash yoki kamera ilovasini o'rnatish shart emas.

Nega RTSP emas, JPEG kadrlar:
  Telefon brauzeri (va Telegram WebView) RTSP ni umuman o'ynatmaydi,
  HLS esa 6-10 soniya kechikadi — nazorat uchun yaramaydi. Shuning
  uchun server ffmpeg bilan oqimni JPEG kadrlarga aylantiradi:

    • to'r (grid) ko'rinishi  — kameraning KICHIK oqimi, 1 kadr/s,
      480px: 20 ta kamerani birvarakayiga ko'rsatsa ham trafik kam;
    • to'liq ekran           — asosiy oqim, 6 kadr/s, 960px, MJPEG
      bo'lib uzatiladi (kechikish ~1 soniya).

ffmpeg faqat KIMDIR KO'RAYOTGANDA ishlaydi: oxirgi so'rovdan 25 soniya
o'tsa jarayon o'zi o'chadi. Aks holda 20 ta kamera serverni doim band
qilib turardi.

Ovoz ALOHIDA oqim bo'lib boradi (`/audio`): MJPEG kadrlari ichiga ovozni
joylab bo'lmaydi, brauzer esa `<img>` dan ovoz chiqarmaydi. Shuning uchun
server o'sha RTSP dan ovozni MP3 (bo'lmasa AAC) qilib uzatadi, Mini App
uni oddiy `<audio>` bilan o'ynatadi. Ovoz faqat tugma bosilganda yoqiladi:
brauzer o'z-o'zidan ovoz chiqarishga ruxsat bermaydi, qolaversa nazoratchi
har kameraga kirganda do'kon shovqini yangrab ketmasin.

Rasm `<img src=...>` bilan yuklanadi, unga header qo'yib bo'lmaydi —
shuning uchun oqim manzili qisqa muddatli TOKEN bilan ochiladi
(`/api/cams/token`, admin sessiyasidan olinadi, 12 soat yashaydi).

Endpointlar:
  GET  /api/cams                    — ro'yxat + oqim tokeni (admin)
  GET  /api/cams/{id}/snap.jpg?t=   — bitta kadr (to'r uchun)
  GET  /api/cams/{id}/live.mjpg?t=  — uzluksiz oqim (to'liq ekran)
  GET  /api/cams/{id}/audio?t=      — kamera mikrofoni (ovozli kameralarda)
  POST /api/admin/cams/save         — qo'shish / tahrirlash
  POST /api/admin/cams/delete       — o'chirish
  POST /api/admin/cams/discover     — tarmoqdan kameralarni topish
  POST /api/admin/cams/probe        — RTSP yo'lini avtomatik aniqlash
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import shutil
import socket
import time
from urllib.parse import quote

from aiohttp import web

logger = logging.getLogger(__name__)
CAM_ROUTES = web.RouteTableDef()


# ─── Sozlamalar ────────────────────────────────────────────────────────────

RTSP_PORT = 554
IDLE_STOP = 25          # shuncha soniya hech kim ko'rmasa — ffmpeg o'chadi
TOKEN_TTL = 12 * 3600   # oqim tokeni shuncha yashaydi
FRAME_WAIT = 12         # birinchi kadrni shuncha kutamiz (kamera sekin ulanadi)
MAX_CAMS = 64

# Oqim profillari: (fps, eni, JPEG sifati, qaysi yo'l)
PROFILES = {
    "grid": {"fps": 1, "width": 480, "q": 8, "path": "sub"},
    "full": {"fps": 6, "width": 960, "q": 4, "path": "main"},
    # Yaqinlashtirilganda (zoom) — kameraning to'liq aniqligi. 960px kadrni
    # 3 barobar kattalashtirsa yozuv ham, odam yuzi ham tanib bo'lmas edi.
    "hi":   {"fps": 4, "width": 1920, "q": 3, "path": "main"},
}

# Kamera brendlari bo'yicha RTSP yo'llari: (asosiy, kichik oqim).
# Avtomatik aniqlashda shular navbat bilan sinaladi — ishlagani saqlanadi.
PATH_TEMPLATES = [
    ("/Streaming/Channels/101", "/Streaming/Channels/102"),          # Hikvision va OEM
    ("/cam/realmonitor?channel=1&subtype=0",
     "/cam/realmonitor?channel=1&subtype=1"),                        # Dahua
    ("/profile1", "/profile2"),                                      # TVT / gSOAP
    ("/h264Preview_01_main", "/h264Preview_01_sub"),                 # Reolink
    ("/live/ch0", "/live/ch1"),
    ("/onvif1", "/onvif2"),
    ("/stream1", "/stream2"),
    ("/11", "/12"),                                                  # XM / Jovision
    ("/live0.264", "/live1.264"),
    ("/ch01/0", "/ch01/1"),
    ("/", ""),                                                       # eng oddiy holat
]


# ─── Baza ──────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    host        TEXT NOT NULL,
    port        INTEGER DEFAULT 554,
    username    TEXT DEFAULT '',
    password    TEXT DEFAULT '',
    main_path   TEXT DEFAULT '',
    sub_path    TEXT DEFAULT '',
    enabled     INTEGER DEFAULT 1,
    sort        INTEGER DEFAULT 0,
    created_at  TEXT
);
"""
_ready = False


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ensure(db) -> None:
    global _ready
    if _ready:
        return
    with db._lock:
        conn = db._conn()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
    _ready = True


def _rows(db) -> list:
    _ensure(db)
    with db._lock:
        conn = db._conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM cameras ORDER BY sort, id").fetchall()]
        finally:
            conn.close()


def _row(db, cid: int) -> dict | None:
    _ensure(db)
    with db._lock:
        conn = db._conn()
        try:
            r = conn.execute("SELECT * FROM cameras WHERE id=?", (cid,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()


def _exec(db, sql: str, args: tuple = ()) -> int:
    _ensure(db)
    with db._lock:
        conn = db._conn()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def _cam_json(c: dict) -> dict:
    """Mini App uchun kamera — PAROL YUBORILMAYDI, faqat bor-yo'qligi."""
    return {
        "id": c["id"],
        "name": c.get("name") or "",
        "host": c.get("host") or "",
        "port": int(c.get("port") or RTSP_PORT),
        "username": c.get("username") or "",
        "has_password": bool(c.get("password")),
        "main_path": c.get("main_path") or "",
        "sub_path": c.get("sub_path") or "",
        "enabled": int(c.get("enabled") or 0) == 1,
        "sort": int(c.get("sort") or 0),
    }


def rtsp_url(c: dict, profile: str = "grid") -> str:
    """Kamera yozuvidan to'liq RTSP manzili.

    Kichik oqim belgilanmagan bo'lsa asosiysi ishlatiladi — sifati
    ortiqcha bo'ladi, lekin to'r baribir ko'rinadi."""
    want_sub = PROFILES.get(profile, PROFILES["grid"])["path"] == "sub"
    path = (c.get("sub_path") if want_sub else "") or c.get("main_path") or ""
    if path and not path.startswith("/"):
        path = "/" + path
    auth = ""
    user = (c.get("username") or "").strip()
    if user:
        auth = quote(user, safe="") + ":" + quote(c.get("password") or "", safe="") + "@"
    port = int(c.get("port") or RTSP_PORT)
    return f"rtsp://{auth}{c['host']}:{port}{path}"


# ─── Oqim tokeni ───────────────────────────────────────────────────────────
# `<img src=...>` ga header qo'yib bo'lmaydi, shuning uchun oqim manzili
# o'zining qisqa muddatli tokeni bilan ochiladi.

_tokens: dict = {}          # token → tugash vaqti


def _issue_token() -> str:
    import secrets
    now = time.time()
    for t, exp in list(_tokens.items()):
        if exp < now:
            _tokens.pop(t, None)
    tok = secrets.token_urlsafe(18)
    _tokens[tok] = now + TOKEN_TTL
    return tok


def _token_ok(tok: str) -> bool:
    exp = _tokens.get(tok or "")
    if not exp:
        return False
    if exp < time.time():
        _tokens.pop(tok, None)
        return False
    return True


# ─── ffmpeg ishchisi: RTSP → JPEG kadrlar ──────────────────────────────────

SOI = b"\xff\xd8"       # JPEG boshi
EOI = b"\xff\xd9"       # JPEG oxiri


class _Worker:
    """Bitta kamera + bitta profil uchun ffmpeg jarayoni.

    Kadrlar quvur (pipe) orqali MJPEG bo'lib keladi; biz ularni
    FFD8...FFD9 belgilari bo'yicha ajratamiz. Oxirgi kadr xotirada
    turadi — snapshot so'rovi darrov javob oladi, kamera qayta
    so'ralmaydi."""

    def __init__(self, key: str, url: str, profile: str):
        self.key = key
        self.url = url
        self.prof = PROFILES.get(profile, PROFILES["grid"])
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.frame: bytes = b""
        self.frame_at: float = 0.0
        self.touched: float = time.time()
        self.error: str = ""
        self.subs: set = set()          # asyncio.Queue — jonli tomoshabinlar
        self.event = asyncio.Event()    # yangi kadr keldi

    # ── hayot sikli ──
    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except Exception:
                pass
        if self.task:
            self.task.cancel()
        self.proc = None

    def touch(self) -> None:
        self.touched = time.time()

    @property
    def idle(self) -> bool:
        return not self.subs and (time.time() - self.touched) > IDLE_STOP

    # ── asosiy halqa ──
    async def _run(self) -> None:
        ff = shutil.which("ffmpeg")
        if not ff:
            self.error = "ffmpeg o'rnatilmagan"
            return
        p = self.prof
        cmd = [
            ff, "-hide_banner", "-loglevel", "error",
            # TCP — Wi-Fi'da UDP kadrlari yo'qoladi va rasm to'kiladi
            "-rtsp_transport", "tcp",
            "-fflags", "nobuffer", "-flags", "low_delay",
            # Soket I/O kutish muddati (mikrosoniya) — kamera javob
            # bermasa ffmpeg ikki daqiqa osilib turmasin.
            "-timeout", "8000000",
            "-i", self.url,
            "-an",
            "-vf", f"fps={p['fps']},scale={p['width']}:-2",
            "-q:v", str(p["q"]),
            "-f", "mjpeg", "pipe:1",
        ]
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
        except Exception as e:                       # pragma: no cover
            self.error = f"ffmpeg ishga tushmadi: {e}"
            return

        err_task = asyncio.create_task(self._drain_err())
        buf = b""
        try:
            while True:
                chunk = await self.proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                # Buferdagi tugallangan kadrlarni ajratamiz
                while True:
                    s = buf.find(SOI)
                    if s < 0:
                        buf = b""
                        break
                    e = buf.find(EOI, s + 2)
                    if e < 0:
                        buf = buf[s:]
                        break
                    self._emit(buf[s:e + 2])
                    buf = buf[e + 2:]
                # Buzuq oqimda bufer cheksiz o'smasin
                if len(buf) > 4 * 1024 * 1024:
                    buf = b""
        except asyncio.CancelledError:
            raise
        except Exception as e:                       # pragma: no cover
            self.error = str(e)
        finally:
            err_task.cancel()
            if not self.error and not self.frame:
                self.error = "kameradan tasvir kelmadi"
            await self.stop()

    async def _drain_err(self) -> None:
        """ffmpeg xatosi — ulanmasa sababini aytib beramiz."""
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    return
                txt = line.decode("utf-8", "replace").strip()
                if txt:
                    self.error = txt[:200]
                    logger.debug("kamera %s: %s", self.key, txt)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _emit(self, jpeg: bytes) -> None:
        self.frame = jpeg
        self.frame_at = time.time()
        self.error = ""
        for q in list(self.subs):
            if q.full():
                try:
                    q.get_nowait()          # eski kadrni tashlaymiz
                except Exception:
                    pass
            try:
                q.put_nowait(jpeg)
            except Exception:
                pass
        self.event.set()
        self.event.clear()

    async def wait_frame(self, timeout: float = FRAME_WAIT) -> bytes:
        """Tayyor kadrni qaytaradi (bor bo'lsa — darrov)."""
        self.touch()
        if self.frame:
            return self.frame
        try:
            await asyncio.wait_for(self.event.wait(), timeout)
        except asyncio.TimeoutError:
            return b""
        return self.frame


_workers: dict = {}
_reaper: asyncio.Task | None = None


async def _reap() -> None:
    """Hech kim ko'rmayotgan kameralarni o'chiradi (tasvir ham, ovoz ham)."""
    while True:
        await asyncio.sleep(5)
        for key, w in list(_workers.items()):
            if w.idle:
                _workers.pop(key, None)
                await w.stop()
        for key, a in list(_audio.items()):
            if a.idle:
                _audio.pop(key, None)
                await a.stop()


async def _worker(cam: dict, profile: str) -> _Worker:
    global _reaper
    if _reaper is None or _reaper.done():
        _reaper = asyncio.create_task(_reap())
    key = f"{cam['id']}:{profile}"
    w = _workers.get(key)
    # Jarayon o'lgan bo'lsa (kamera uzilib qayta ulangan) — qaytadan
    if w and w.task and w.task.done():
        _workers.pop(key, None)
        w = None
    if w is None:
        w = _Worker(key, rtsp_url(cam, profile), profile)
        _workers[key] = w
        await w.start()
    w.touch()
    return w


def stop_all() -> None:
    """Bot o'chayotganda chaqiriladi."""
    for w in list(_workers.values()) + list(_audio.values()):
        if w.proc and w.proc.returncode is None:
            try:
                w.proc.kill()
            except Exception:
                pass
    _workers.clear()
    _audio.clear()


# ─── Ovoz: RTSP → MP3 (yoki AAC) ───────────────────────────────────────────
# Nega alohida jarayon: tasvir kichik oqimdan, 1-6 kadr/s bilan olinadi va
# ovoz uchun yaramaydi; qolaversa ovoz HAR DOIM emas, faqat tugma bosilganda
# kerak. Shuning uchun ovoz o'z ffmpeg'ida, o'z hayot sikli bilan yuradi.

AUDIO_IDLE = 12          # shuncha soniya hech kim eshitmasa — o'chadi
_acodec: tuple | None = None       # (kodek, format, content-type)


async def _audio_codec() -> tuple:
    """Shu serverdagi ffmpeg nimani kodlay oladi: MP3 yoki AAC.

    MP3 ni hamma brauzer (iPhone ham) oqim holida o'ynaydi, lekin ba'zi
    ffmpeg yig'malarida `libmp3lame` bo'lmaydi — o'shanda AAC (ADTS)."""
    global _acodec
    if _acodec:
        return _acodec
    ff = shutil.which("ffmpeg")
    txt = ""
    if ff:
        try:
            pr = await asyncio.create_subprocess_exec(
                ff, "-hide_banner", "-encoders",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(pr.communicate(), 10)
            txt = out.decode("utf-8", "replace")
        except Exception:                            # pragma: no cover
            txt = ""
    _acodec = (("libmp3lame", "mp3", "audio/mpeg") if "libmp3lame" in txt
               else ("aac", "adts", "audio/aac"))
    return _acodec


class _AudioWorker:
    """Bitta kameraning ovozi. Eshitayotganlarga bo'laklab tarqatiladi."""

    def __init__(self, key: str, url: str, enc: tuple):
        self.key = key
        self.url = url
        self.enc = enc
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.subs: set = set()
        self.error: str = ""
        self.first: bytes = b""
        self.event = asyncio.Event()
        self.touched = time.time()

    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except Exception:
                pass
        if self.task:
            self.task.cancel()
        self.proc = None

    def touch(self) -> None:
        self.touched = time.time()

    @property
    def idle(self) -> bool:
        return not self.subs and (time.time() - self.touched) > AUDIO_IDLE

    async def _run(self) -> None:
        ff = shutil.which("ffmpeg")
        if not ff:
            self.error = "ffmpeg o'rnatilmagan"
            self.event.set()
            return
        codec, fmt, _ctype = self.enc
        cmd = [
            ff, "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-fflags", "nobuffer",
            "-timeout", "8000000",
            "-i", self.url,
            "-vn", "-ac", "1", "-ar", "22050",
            "-c:a", codec, "-b:a", "48k",
            "-f", fmt, "-flush_packets", "1", "pipe:1",
        ]
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
        except Exception as e:                       # pragma: no cover
            self.error = f"ffmpeg ishga tushmadi: {e}"
            self.event.set()
            return
        err_task = asyncio.create_task(self._drain_err())
        try:
            while True:
                chunk = await self.proc.stdout.read(4096)
                if not chunk:
                    break
                if not self.first:
                    self.first = chunk
                    self.event.set()
                for q in list(self.subs):
                    if q.full():
                        try:
                            q.get_nowait()       # kechikkan tomoshabin — eskisi tashlanadi
                        except Exception:
                            pass
                    try:
                        q.put_nowait(chunk)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as e:                       # pragma: no cover
            self.error = str(e)
        finally:
            err_task.cancel()
            if not self.first and not self.error:
                self.error = "kameradan ovoz kelmadi"
            self.event.set()
            await self.stop()

    async def _drain_err(self) -> None:
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    return
                txt = line.decode("utf-8", "replace").strip()
                if not txt:
                    continue
                # Ovozsiz kamera: "Output file #0 does not contain any stream"
                if "does not contain any stream" in txt or "Audio: none" in txt:
                    self.error = "bu kamerada ovoz yo'q"
                else:
                    self.error = txt[:200]
                self.event.set()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def wait_start(self, timeout: float = 12.0) -> bytes:
        """Birinchi bo'lak kelguncha kutadi (ffmpeg kamerani ulashi vaqt oladi)."""
        self.touch()
        if self.first:
            return self.first
        try:
            await asyncio.wait_for(self.event.wait(), timeout)
        except asyncio.TimeoutError:
            return b""
        return self.first


_audio: dict = {}


async def _audio_worker(cam: dict, enc: tuple) -> _AudioWorker:
    global _reaper
    if _reaper is None or _reaper.done():
        _reaper = asyncio.create_task(_reap())
    key = str(cam["id"])
    a = _audio.get(key)
    if a and a.task and a.task.done() and not a.subs:
        _audio.pop(key, None)
        a = None
    if a is None:
        # Ovoz ASOSIY oqimda bo'ladi — kichik oqimda ko'pincha ovoz yo'q.
        a = _AudioWorker(key, rtsp_url(cam, "full"), enc)
        _audio[key] = a
        await a.start()
    a.touch()
    return a


# ─── Tarmoqdan kamera izlash ───────────────────────────────────────────────

def _local_subnet() -> str:
    """Serverning do'kon tarmog'idagi manzili → '192.168.1' kabi prefiks."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip.rsplit(".", 1)[0]


async def _port_open(host: str, port: int, timeout: float = 1.2) -> bool:
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def discover(subnet: str = "") -> list:
    """Tarmoqdagi RTSP (554-port) qurilmalarini topadi.

    Bu — do'konning O'Z tarmog'i va admin tugmani bosgandagina ishlaydi.
    Faqat port ochiqligi tekshiriladi, hech narsa so'ralmaydi."""
    prefix = (subnet or _local_subnet()).strip().rstrip(".")
    try:
        ipaddress.ip_address(prefix + ".1")
    except ValueError:
        return []
    sem = asyncio.Semaphore(80)

    async def one(i: int):
        host = f"{prefix}.{i}"
        async with sem:
            if await _port_open(host, RTSP_PORT):
                return host
        return None

    found = await asyncio.gather(*(one(i) for i in range(1, 255)))
    return [h for h in found if h]


async def probe(cam: dict, timeout: float = 8.0) -> tuple:
    """Kameraning RTSP yo'lini aniqlaydi: (main, sub, xato).

    Har brendning o'z yo'li bor va uni oldindan bilib bo'lmaydi —
    shuning uchun shablonlar navbat bilan sinaladi: ffmpeg bitta kadr
    ola bilsa, o'sha yo'l to'g'ri."""
    ff = shutil.which("ffmpeg")
    if not ff:
        return "", "", "ffmpeg o'rnatilmagan"
    last = ""
    for main, sub in PATH_TEMPLATES:
        test = dict(cam)
        test["main_path"] = main
        test["sub_path"] = ""
        ok, err = await _try_url(ff, rtsp_url(test, "full"), timeout)
        if not ok:
            last = err or last
            continue
        # Asosiysi ishladi — kichik oqimni ham tekshiramiz (bo'lmasa zarar yo'q)
        sub_ok = False
        if sub:
            test["sub_path"] = sub
            sub_ok, _ = await _try_url(ff, rtsp_url(test, "grid"), timeout)
        return main, (sub if sub_ok else ""), ""
    return "", "", (last or "kamera javob bermadi")


async def _try_url(ff: str, url: str, timeout: float) -> tuple:
    """Shu RTSP manzilidan bitta kadr olinadimi?"""
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
           "-timeout", str(int(timeout * 1_000_000)), "-i", url,
           "-frames:v", "1", "-f", "image2", "-q:v", "9", "pipe:1"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except Exception as e:
        return False, str(e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout + 4)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return False, "vaqt tugadi"
    if out[:2] == SOI:
        return True, ""
    msg = (err or b"").decode("utf-8", "replace").strip().splitlines()
    return False, (msg[-1][:160] if msg else "tasvir kelmadi")


def _mask_url(text: str) -> str:
    """Xato matnidagi parolni yashiradi (log va UI uchun)."""
    return re.sub(r"rtsp://[^:/@\s]+:[^@\s]+@", "rtsp://***:***@", text or "")


# ─── Endpointlar: ro'yxat va sozlash ───────────────────────────────────────

@CAM_ROUTES.get("/api/cams")
async def api_list(request: web.Request):
    """Kameralar ro'yxati + oqim tokeni. Ruxsat: 'cams'."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard
    auth, err = await _admin_guard(request, _db, "cams")
    if err is not None:
        return err
    cams = [_cam_json(c) for c in _rows(_db)]
    live = {int(k.split(":")[0]) for k, w in _workers.items() if w.frame}
    for c in cams:
        c["live"] = c["id"] in live
    return web.json_response({
        "ok": True,
        "cams": cams,
        "token": _issue_token(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "subnet": _local_subnet(),
    })


@CAM_ROUTES.post("/api/admin/cams/save")
async def api_save(request: web.Request):
    """Kamera qo'shish (id=0) yoki tahrirlash. Ruxsat: 'cams'."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard, _err, _read_json
    auth, err = await _admin_guard(request, _db, "cams")
    if err is not None:
        return err
    body = await _read_json(request)
    cid = int(body.get("id") or 0)
    name = (body.get("name") or "").strip()
    host = (body.get("host") or "").strip()
    if not host:
        return _err("Kamera IP manzili kiritilmagan")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.match(r"^[A-Za-z0-9.\-]+$", host):
            return _err("IP manzil noto'g'ri")
    if not name:
        name = f"Kamera {host.rsplit('.', 1)[-1]}"
    port = int(body.get("port") or RTSP_PORT)
    enabled = 1 if body.get("enabled", True) else 0
    sort = int(body.get("sort") or 0)

    def _keep(field: str, old_row: dict) -> str:
        """Yuborilmagan maydon o'chib ketmasin — eskisi qoladi.

        Parolni UI ko'rsatmaydi, oqim yo'lini esa `probe` o'zi topadi:
        ikkalasi ham formadan bo'sh kelishi mumkin, lekin bu «o'chir»
        degani emas."""
        v = body.get(field)
        return (old_row.get(field) or "") if v is None else str(v).strip()

    if cid:
        prev = _row(_db, cid)
        if not prev:
            return _err("Kamera topilmadi", 404)
        _exec(_db, "UPDATE cameras SET name=?, host=?, port=?, username=?, password=?,"
                   " main_path=?, sub_path=?, enabled=?, sort=? WHERE id=?",
              (name, host, port, _keep("username", prev), _keep("password", prev),
               _keep("main_path", prev), _keep("sub_path", prev), enabled, sort, cid))
    else:
        if len(_rows(_db)) >= MAX_CAMS:
            return _err(f"Kameralar soni {MAX_CAMS} tadan oshmasin")
        cid = _exec(_db, "INSERT INTO cameras(name, host, port, username, password,"
                         " main_path, sub_path, enabled, sort, created_at)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (name, host, port, _keep("username", {}), _keep("password", {}),
                     _keep("main_path", {}), _keep("sub_path", {}),
                     enabled, sort, _now()))
    # Sozlama o'zgardi — ishlab turgan oqim eski manzilda qolmasin
    for key in [k for k in _workers if k.startswith(f"{cid}:")]:
        w = _workers.pop(key)
        await w.stop()
    return web.json_response({"ok": True, "cam": _cam_json(_row(_db, cid))})


@CAM_ROUTES.post("/api/admin/cams/delete")
async def api_delete(request: web.Request):
    """Kamerani ro'yxatdan o'chirish. Ruxsat: 'cams'."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard, _err, _read_json
    auth, err = await _admin_guard(request, _db, "cams")
    if err is not None:
        return err
    cid = int((await _read_json(request)).get("id") or 0)
    if not cid:
        return _err("id yo'q")
    for key in [k for k in _workers if k.startswith(f"{cid}:")]:
        w = _workers.pop(key)
        await w.stop()
    _exec(_db, "DELETE FROM cameras WHERE id=?", (cid,))
    return web.json_response({"ok": True})


@CAM_ROUTES.post("/api/admin/cams/discover")
async def api_discover(request: web.Request):
    """Tarmoqdan kameralarni izlash — topilganlar ro'yxati.
    Allaqachon qo'shilganlari `added: true` bilan belgilanadi."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard, _read_json
    auth, err = await _admin_guard(request, _db, "cams")
    if err is not None:
        return err
    body = await _read_json(request)
    hosts = await discover((body.get("subnet") or "").strip())
    have = {c.get("host") for c in _rows(_db)}
    mine = _local_subnet() + "."
    return web.json_response({
        "ok": True,
        "subnet": (body.get("subnet") or _local_subnet()),
        "found": [{"host": h, "added": h in have} for h in hosts
                  if not h.startswith(mine + "0")],
    })


@CAM_ROUTES.post("/api/admin/cams/probe")
async def api_probe(request: web.Request):
    """Kameraning RTSP yo'lini aniqlash va saqlash.

    `id` berilsa — bazadagi kamera, aks holda yuborilgan ma'lumot
    (host/username/password) sinab ko'riladi."""
    from database.channel_db import db as _db
    from bot.web_api import _admin_guard, _err, _read_json
    auth, err = await _admin_guard(request, _db, "cams")
    if err is not None:
        return err
    body = await _read_json(request)
    cid = int(body.get("id") or 0)
    if cid:
        cam = _row(_db, cid)
        if not cam:
            return _err("Kamera topilmadi", 404)
        if body.get("password") is not None:
            cam["password"] = str(body.get("password"))
        if body.get("username") is not None:
            cam["username"] = str(body.get("username")).strip()
    else:
        host = (body.get("host") or "").strip()
        if not host:
            return _err("IP manzil yo'q")
        cam = {"id": 0, "host": host, "port": int(body.get("port") or RTSP_PORT),
               "username": (body.get("username") or "").strip(),
               "password": body.get("password") or "",
               "main_path": "", "sub_path": ""}
    main, sub, perr = await probe(cam)
    if not main:
        return web.json_response({"ok": False, "error": _mask_url(perr)})
    if cid:
        _exec(_db, "UPDATE cameras SET main_path=?, sub_path=?, username=?, password=?"
                   " WHERE id=?", (main, sub, cam.get("username") or "",
                                   cam.get("password") or "", cid))
        for key in [k for k in _workers if k.startswith(f"{cid}:")]:
            w = _workers.pop(key)
            await w.stop()
    return web.json_response({"ok": True, "main_path": main, "sub_path": sub})


# ─── Endpointlar: tasvir ───────────────────────────────────────────────────

async def _cam_for_stream(request: web.Request):
    """Oqim so'rovini tekshiradi: token + kamera. (cam, profile, xato)."""
    from database.channel_db import db as _db
    if not _token_ok(request.query.get("t", "")):
        raise web.HTTPUnauthorized(text="token eskirgan")
    cid = int(request.match_info.get("id") or 0)
    cam = _row(_db, cid)
    if not cam or not int(cam.get("enabled") or 0):
        raise web.HTTPNotFound(text="kamera topilmadi")
    if not (cam.get("main_path") or cam.get("sub_path")):
        raise web.HTTPNotFound(text="kamera sozlanmagan")
    profile = request.query.get("p", "grid")
    if profile not in PROFILES:
        profile = "grid"
    return cam, profile


@CAM_ROUTES.get("/api/cams/{id}/snap.jpg")
async def api_snap(request: web.Request):
    """Bitta kadr — to'r ko'rinishi shuni takror so'rab turadi."""
    cam, profile = await _cam_for_stream(request)
    w = await _worker(cam, profile)
    jpeg = await w.wait_frame()
    if not jpeg:
        return web.json_response(
            {"ok": False, "error": _mask_url(w.error or "tasvir yo'q")}, status=503)
    return web.Response(body=jpeg, content_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


@CAM_ROUTES.get("/api/cams/{id}/live.mjpg")
async def api_live(request: web.Request):
    """Uzluksiz oqim (MJPEG) — to'liq ekran uchun.

    Brauzer buni oddiy `<img>` da o'ynatadi; har kadr alohida so'rov
    bo'lmaydi, shuning uchun kechikish kam."""
    cam, profile = await _cam_for_stream(request)
    w = await _worker(cam, profile)
    first = await w.wait_frame()
    if not first:
        return web.json_response(
            {"ok": False, "error": _mask_url(w.error or "tasvir yo'q")}, status=503)

    boundary = "chinorcam"
    resp = web.StreamResponse(headers={
        "Content-Type": f"multipart/x-mixed-replace; boundary={boundary}",
        "Cache-Control": "no-store",
        "Connection": "close",
    })
    await resp.prepare(request)
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    w.subs.add(q)
    try:
        frame = first
        while True:
            w.touch()
            await resp.write(
                b"--" + boundary.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                + frame + b"\r\n")
            frame = await asyncio.wait_for(q.get(), timeout=20)
    except (asyncio.TimeoutError, asyncio.CancelledError,
            ConnectionResetError, ConnectionError):
        pass
    except Exception as e:                            # pragma: no cover
        logger.debug("kamera oqimi uzildi: %s", e)
    finally:
        w.subs.discard(q)
        w.touch()
    return resp


@CAM_ROUTES.get("/api/cams/{id}/audio")
async def api_audio(request: web.Request):
    """Kamera mikrofoni — uzluksiz MP3 (yoki AAC) oqimi.

    Mini App buni `<audio>` bilan o'ynatadi. Ovozsiz kamerada ffmpeg
    darrov to'xtaydi va biz sababni aytamiz — tugma jim qotib qolmasin."""
    cam, _profile = await _cam_for_stream(request)
    enc = await _audio_codec()
    a = await _audio_worker(cam, enc)
    first = await a.wait_start()
    if not first:
        return web.json_response(
            {"ok": False, "error": _mask_url(a.error or "ovoz yo'q")}, status=503)

    resp = web.StreamResponse(headers={
        "Content-Type": enc[2],
        "Cache-Control": "no-store",
        "Connection": "close",
    })
    await resp.prepare(request)
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    a.subs.add(q)
    try:
        await resp.write(first)
        while True:
            a.touch()
            chunk = await asyncio.wait_for(q.get(), timeout=20)
            await resp.write(chunk)
    except (asyncio.TimeoutError, asyncio.CancelledError,
            ConnectionResetError, ConnectionError):
        pass
    except Exception as e:                            # pragma: no cover
        logger.debug("kamera ovozi uzildi: %s", e)
    finally:
        a.subs.discard(q)
        a.touch()
    return resp


async def _on_cleanup(app: web.Application) -> None:
    """Server to'xtaganda ffmpeg jarayonlari osilib qolmasin."""
    stop_all()


def setup(app: web.Application):
    app.add_routes(CAM_ROUTES)
    app.on_cleanup.append(_on_cleanup)
