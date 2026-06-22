'use strict';
/* Minimal Code128-B barcode → SVG (offline, tashqi kutubxonasiz).
   Sennik (narx teglari) skanerlanadigan bo'lishi uchun. */
(function () {
  // Code128 naqshlar jadvali (qiymat 0..106). Har biri: bar/space modul kengliklari.
  const PATTERNS = [
    '212222','222122','222221','121223','121322','131222','122213','122312','132212','221213',
    '221312','231212','112232','122132','122231','113222','123122','123221','223211','221132',
    '221231','213212','223112','312131','311222','321122','321221','312212','322112','322211',
    '212123','212321','232121','111323','131123','131321','112313','132113','132311','211313',
    '231113','231311','112133','112331','132131','113123','113321','133121','313121','211331',
    '231131','213113','213311','213131','311123','311321','331121','312113','312311','332111',
    '314111','221411','431111','111224','111422','121124','121421','141122','141221','112214',
    '112412','122114','122411','142112','142211','241211','221114','413111','241112','134111',
    '111242','121142','121241','114212','124112','124211','411212','421112','421211','212141',
    '214121','412121','111143','111341','131141','114113','114311','411113','411311','113141',
    '114131','311141','411131','211412','211214','211232','2331112',
  ];
  const START_B = 104, STOP = 106;

  // Code128-B: ASCII 32..126 ni qo'llab-quvvatlaydi. Boshqa belgilar tashlanadi.
  function encode(text) {
    const chars = [];
    for (const ch of String(text)) {
      const code = ch.charCodeAt(0);
      if (code >= 32 && code <= 126) chars.push(code - 32);
    }
    if (!chars.length) return null;
    const codes = [START_B, ...chars];
    let sum = START_B;
    chars.forEach((v, i) => { sum += v * (i + 1); });
    codes.push(sum % 103);   // checksum
    codes.push(STOP);
    return codes.map((c) => PATTERNS[c]).join('');
  }

  // text → SVG (string). opts: { height, module, withText, quiet }
  function toSVG(text, opts = {}) {
    const pat = encode(text);
    if (!pat) return '';
    const m = opts.module || 2;       // bitta modul kengligi (px)
    const h = opts.height || 56;       // barcode balandligi
    const quiet = (opts.quiet == null ? 10 : opts.quiet); // chap/o'ng bo'sh zona (modul)
    const showText = opts.withText !== false;
    const textH = showText ? 16 : 0;

    let x = quiet * m;
    let bars = '';
    let isBar = true;
    for (const d of pat) {
      const w = parseInt(d, 10) * m;
      if (isBar) bars += `<rect x="${x}" y="0" width="${w}" height="${h}" fill="#000"/>`;
      x += w;
      isBar = !isBar;
    }
    const totalW = x + quiet * m;
    const fullH = h + textH;
    let label = '';
    if (showText) {
      label = `<text x="${totalW / 2}" y="${h + 13}" text-anchor="middle" font-family="monospace" font-size="13" fill="#000" letter-spacing="1">${String(text)}</text>`;
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${totalW} ${fullH}" width="${totalW}" height="${fullH}" shape-rendering="crispEdges"><rect width="${totalW}" height="${fullH}" fill="#fff"/>${bars}${label}</svg>`;
  }

  window.Code128 = { toSVG, encode };
})();
