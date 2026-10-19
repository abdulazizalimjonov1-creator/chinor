"""Rolik uchun ovoz — musiqa va effektlar SINTEZ qilinadi.

Nega tayyor trek emas: internetdan olingan musiqa mualliflik huquqi bilan
himoyalangan, Instagram uni aniqlaydi va rolikning ovozini o'chiradi yoki
postni cheklaydi. Bu yerdagi ovoz to'liq o'zimizniki — hech kimning
huquqi buzilmaydi va hech qachon bloklanmaydi.

Nima yasaladi:
  • yumshoq akkord «yostig'i» (pad) — to'rtta akkord aylanadi;
  • ustidan tinch arpeggio (pluck) — ritm beradi;
  • past bochka (kick) va yengil hi-hat — yurish hissi;
  • sahna almashganda «shuvillash» (whoosh);
  • narx chiqqanda «muhr» zarbi.

Instagram'da o'z modali musiqasini qo'shmoqchi bo'lsangiz — bu ovozni
ilovaning o'zida pasaytirib qo'yish mumkin.
"""

from __future__ import annotations

import wave
import struct
import logging

logger = logging.getLogger(__name__)

SR = 44100

# Am → F → C → G: eng keng tarqalgan, quloqqa yoqimli aylanma
_CHORDS = [
    (220.00, 261.63, 329.63),   # Am
    (174.61, 220.00, 261.63),   # F
    (261.63, 329.63, 392.00),   # C
    (196.00, 246.94, 293.66),   # G
]


def _env(n, attack, decay, np):
    """Oddiy tovush o'rami: tez ko'tarilib, sekin so'nadi."""
    a = max(1, int(attack * SR))
    e = np.ones(n, dtype="float32")
    a = min(a, n)
    e[:a] = np.linspace(0, 1, a, dtype="float32")
    d = np.exp(-np.linspace(0, decay, n - a, dtype="float32"))
    e[a:] = d
    return e


def build(path: str, duration: float, cuts=None, stamp: float = None) -> bool:
    """Ovoz faylini yasaydi (WAV). `cuts` — sahna almashish vaqtlari,
    `stamp` — narx chiqadigan vaqt. numpy bo'lmasa False."""
    try:
        import numpy as np
    except Exception as e:
        logger.info("🎬 Ovoz yasalmadi (numpy yo'q): %s", e)
        return False

    n = int(duration * SR)
    t = np.arange(n, dtype="float32") / SR
    mix = np.zeros(n, dtype="float32")

    bar = 3.25                      # bitta akkord necha soniya turadi
    beat = bar / 4.0

    # ── Akkord yostig'i ───────────────────────────────────────────────────
    for i in range(int(duration / bar) + 1):
        s = int(i * bar * SR)
        e = min(n, int((i * bar + bar + 0.35) * SR))
        if s >= n:
            break
        seg = np.arange(e - s, dtype="float32") / SR
        env = _env(e - s, 0.25, 2.2, np) * 0.13
        for f in _CHORDS[i % len(_CHORDS)]:
            # ikki ovoz bir-biridan sal farqli — "jonli" eshitiladi
            mix[s:e] += env * (np.sin(2 * np.pi * f * seg)
                               + 0.5 * np.sin(2 * np.pi * f * 1.004 * seg)) * .5

    # ── Arpeggio ──────────────────────────────────────────────────────────
    step = beat / 2.0               # sakkizlik
    k = 0
    while k * step < duration:
        ch = _CHORDS[int((k * step) / bar) % len(_CHORDS)]
        f = ch[k % 3] * 2           # bir oktava yuqori
        s = int(k * step * SR)
        e = min(n, s + int(0.30 * SR))
        if s >= n:
            break
        seg = np.arange(e - s, dtype="float32") / SR
        mix[s:e] += (_env(e - s, 0.004, 9.0, np) * 0.09
                     * np.sin(2 * np.pi * f * seg))
        k += 1

    # ── Bochka va hi-hat ──────────────────────────────────────────────────
    b = 0
    while b * beat < duration:
        s = int(b * beat * SR)
        if b % 4 in (0, 2):         # kick
            e = min(n, s + int(0.18 * SR))
            seg = np.arange(e - s, dtype="float32") / SR
            f = 110 * np.exp(-seg * 28) + 45      # tushuvchi ton
            mix[s:e] += _env(e - s, 0.002, 14.0, np) * 0.30 * np.sin(2 * np.pi * f * seg)
        if b % 2 == 1:              # hi-hat
            e = min(n, s + int(0.05 * SR))
            noise = np.random.RandomState(b).uniform(-1, 1, e - s).astype("float32")
            noise = np.diff(np.concatenate(([0], noise)))   # yuqori chastota
            mix[s:e] += _env(e - s, 0.001, 22.0, np) * 0.05 * noise
        b += 1

    # ── Sahna almashuvi: shuvillash ───────────────────────────────────────
    for c in (cuts or []):
        s = int(max(0.0, c - 0.35) * SR)
        e = min(n, s + int(0.55 * SR))
        if s >= n or e <= s:
            continue
        seg = np.arange(e - s, dtype="float32") / SR
        noise = np.random.RandomState(int(c * 100)).uniform(-1, 1, e - s).astype("float32")
        # ko'tariluvchi ton + shovqin = "o'tish" hissi
        sweep = np.sin(2 * np.pi * (300 + 1400 * (seg / seg[-1])) * seg)
        env = np.sin(np.pi * seg / seg[-1]) ** 2
        mix[s:e] += env * (0.10 * noise + 0.05 * sweep)

    # ── Narx «muhri» ──────────────────────────────────────────────────────
    if stamp is not None:
        s = int(stamp * SR)
        e = min(n, s + int(0.45 * SR))
        if e > s:
            seg = np.arange(e - s, dtype="float32") / SR
            thud = np.sin(2 * np.pi * (90 * np.exp(-seg * 20) + 55) * seg)
            click = np.random.RandomState(7).uniform(-1, 1, e - s).astype("float32")
            mix[s:e] += _env(e - s, 0.002, 16.0, np) * (0.34 * thud + 0.05 * click)

    # ── Yakun: oxirida yumshoq so'nish, klipdan himoya ────────────────────
    fade = int(min(1.2, duration * 0.15) * SR)
    if fade > 0:
        mix[-fade:] *= np.linspace(1, 0, fade, dtype="float32")
    peak = float(np.max(np.abs(mix))) or 1.0
    mix = np.tanh(mix / peak * 1.2) * 0.72

    data = (mix * 32767).astype("<i2")
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(data.tobytes())
    except Exception as e:
        logger.warning("🎬 Ovoz fayli yozilmadi: %s", e)
        return False
    return True
