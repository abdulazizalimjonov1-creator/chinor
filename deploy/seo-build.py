#!/usr/bin/env python3
"""Saytga tovar sahifalarini yasaydi — Google tovar nomi bo'yicha topsin.

Ishlatish (Mac'dan, loyiha ildizida):

    python3 deploy/seo-build.py            # site/ ichiga yozadi
    bash deploy/site-deploy.sh             # yasaydi va Cloudflare'ga chiqaradi

Nima qiladi:
  • ochiq /api/shop/catalog dan tovarlarni oladi — u yerda FAQAT o'z surati
    bor tovarlar bo'ladi, ya'ni yangi tovarga surat qo'ysangiz, keyingi
    ishga tushirishda sahifasi o'zi paydo bo'ladi;
  • har tovarga  site/t/<nom>-<id>.html
  • har bo'limga site/b/<bolim>.html
  • hammasining ro'yxati: site/tovarlar/index.html
  • rasmlarni site/img/ ga yuklab oladi — server/tunnel o'chsa ham sahifa
    rasm bilan ochiladi va Google rasmni indeksga oladi;
  • site/sitemap.xml ni qaytadan yozadi.

Qayta ishga tushirsa bo'ladi: t/, b/, tovarlar/ qaytadan yasaladi, img/
esa keshlanadi (bor rasm ikkinchi marta yuklanmaydi).

BASE_URL — sayt manzili. Domen ulangach o'zgartirish SHU YERDA, bir joyda.
"""
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

API = os.environ.get("CHINOR_API", "https://kassa.chinorpos.com")
BASE_URL = os.environ.get("BASE_URL", "https://chinor.chinorpos.com").rstrip("/")
ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
TODAY = date.today().isoformat()

# ── Nomdan manzil yasash ────────────────────────────────────────────────────
# Kirilcha ham, o'zbekcha ham bitta qoida bilan lotin harfga aylanadi:
# "Gipsokarton stenavoy" → "gipsokarton-stenavoy"
TRANS = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ғ": "g", "қ": "q", "ҳ": "h", "ў": "o", "ʻ": "", "ʼ": "", "'": "",
    "`": "", "’": "", "‘": "",
}


def slug(text: str) -> str:
    s = (text or "").lower()
    s = "".join(TRANS.get(ch, ch) for ch in s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "tovar"


def esc(text) -> str:
    return (str(text or "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def money(value) -> str:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return ""
    return f"{n:,}".replace(",", " ") + " so'm"


# ── Ma'lumotni olish ────────────────────────────────────────────────────────
def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "chinor-seo-build"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_catalog() -> tuple:
    items, page, shop = [], 0, {}
    while True:
        data = get_json(f"{API}/api/shop/catalog?limit=60&page={page}")
        if not data.get("ok"):
            raise SystemExit("API javob bermadi: " + str(data)[:200])
        shop = data.get("shop") or shop
        items += data.get("items") or []
        if not data.get("has_more"):
            break
        page += 1
        if page > 200:            # cheksiz aylanishdan himoya
            break
    return items, shop


def fetch_image(path: str, out_dir: Path) -> str:
    """Rasmni site/img/ ga yuklaydi, sahifa uchun manzilini qaytaradi."""
    if not path:
        return ""
    name = os.path.basename(urllib.parse.urlparse(path).path)
    dest = out_dir / name
    if not dest.exists():
        url = path if path.startswith("http") else API + path
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "chinor-seo-build"})
            with urllib.request.urlopen(req, timeout=60) as r:
                blob = r.read()
            if len(blob) < 600:   # bo'sh yoki xato sahifa
                return ""
            dest.write_bytes(blob)
        except Exception as e:                      # noqa: BLE001
            print(f"  ! rasm olinmadi {name}: {e}")
            return ""
    return "/img/" + name


# ── Sahifa qolipi ───────────────────────────────────────────────────────────
CSS = """*{box-sizing:border-box}
:root{--green:#2d6a4f;--green-d:#1b4332;--green-l:#e8f5ec;--bg:#f7f9f7;
--card:#fff;--text:#16241c;--muted:#68796f;--line:#e4ebe6}
@media (prefers-color-scheme:dark){:root{--bg:#0e1512;--card:#152019;--text:#e8f0ea;
--muted:#94a99c;--line:#22322a;--green-l:#17251d}}
body{margin:0;background:var(--bg);color:var(--text);line-height:1.6;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:inherit;text-decoration:none}
img{max-width:100%;display:block}
.wrap{max-width:900px;margin:0 auto;padding:0 18px}
header{background:var(--card);border-bottom:1px solid var(--line);padding:12px 0}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px}
.brand img{width:36px;height:36px;border-radius:10px}
.crumbs{font-size:13px;color:var(--muted);padding:16px 0 4px}
.crumbs a:hover{color:var(--green)}
h1{font-size:clamp(22px,4vw,30px);line-height:1.25;margin:10px 0 6px}
.cat{display:inline-block;font-size:13px;font-weight:700;color:var(--green);
background:var(--green-l);padding:4px 10px;border-radius:999px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
overflow:hidden;margin-bottom:18px}
.photo{background:#fff;display:flex;align-items:center;justify-content:center;padding:14px}
.photo img{max-height:420px;width:auto;object-fit:contain}
.pad{padding:16px}
.price{font-size:26px;font-weight:800;color:var(--green-d)}
@media (prefers-color-scheme:dark){.price{color:#74c69d}}
.stock{font-size:14px;color:var(--muted);margin-top:4px}
.desc{white-space:pre-line;margin:14px 0 0}
.btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
.btn{background:var(--green);color:#fff;padding:12px 18px;border-radius:12px;
font-weight:700;font-size:15px}
.btn.alt{background:var(--card);color:var(--text);border:1.5px solid var(--line)}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
.it{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.it img{height:140px;width:100%;object-fit:contain;background:#fff;padding:8px}
.it b{display:block;padding:10px 10px 2px;font-size:14px;font-weight:600;line-height:1.35}
.it span{display:block;padding:0 10px 10px;font-size:13px;color:var(--green);font-weight:700}
h2{font-size:19px;margin:26px 0 12px}
.lst{columns:2;column-gap:22px;font-size:14.5px}
.lst a{display:block;padding:4px 0;break-inside:avoid;color:var(--muted)}
.lst a:hover{color:var(--green)}
@media(max-width:600px){.lst{columns:1}}
footer{border-top:1px solid var(--line);margin-top:34px;padding:20px 0 40px;
color:var(--muted);font-size:14px}
"""

HEAD = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="{ogtype}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{img}">
<meta name="theme-color" content="#1b4332">
<link rel="icon" href="/logo.png">
<link rel="stylesheet" href="/assets/tovar.css">
</head>
<body>
<header><div class="wrap"><a class="brand" href="/"><img src="/logo.png" alt="Chinor">
Chinor qurilish do'koni</a></div></header>
<div class="wrap">
"""

FOOT = """</div>
<footer><div class="wrap">
<b>Chinor qurilish do'koni</b> — {location}<br>
Har kuni 8:00 — 22:00 · <a href="tel:{tel_raw}">{tel}</a><br>
<a href="/">Bosh sahifa</a> · <a href="/tovarlar/">Barcha tovarlar</a>
</div></footer>
</body>
</html>
"""


def tile(o) -> str:
    """Ro'yxatlardagi bitta kartochka."""
    img = ""
    if o["img"]:
        img = '<img src="%s" alt="%s" loading="lazy">' % (esc(o["img"]), esc(o["name"]))
    return ('<a class="it" href="/t/%s">%s<b>%s</b><span>%s</span></a>\n'
            % (o["slug"], img, esc(o["name"]), esc(money(o.get("price")))))


def page(path: Path, title, desc, url, body, img, shop, ogtype="website"):
    tel = (shop.get("phones") or ["+998 77 877 55 33"])[0]
    html = HEAD.format(title=esc(title), desc=esc(desc), url=esc(url),
                       img=esc(img or BASE_URL + "/logo.png"), ogtype=ogtype)
    html += body
    html += FOOT.format(location=esc(shop.get("location") or "Sergeli, Toshkent"),
                        tel=esc(tel), tel_raw=esc(tel.replace(" ", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def product_page(it, shop, same_cat):
    url = f"{BASE_URL}/t/{it['slug']}"
    img_abs = BASE_URL + it["img"] if it["img"] else ""
    price = money(it.get("price"))
    instock = (it.get("qty") or 0) > 0
    cat = it.get("category") or ""
    # Sarlavha — odam Google'da aynan shunday qidiradi: «<nom> narxi Toshkent»
    title = f"{it['name']} — narxi {price} | Chinor, Sergeli"
    desc = (f"{it['name']}. Narxi {price}. {cat} bo'limi. "
            f"Chinor qurilish do'koni, Sergeli, Chinor bekati yoni. "
            f"Har kuni 8:00–22:00, yetkazib berish bor.")
    tel = (shop.get("phones") or [""])[0]
    bot = shop.get("bot") or ""

    ld = {
        "@context": "https://schema.org", "@type": "Product",
        "name": it["name"], "description": (it.get("description") or desc)[:900],
        "sku": str(it["id"]), "category": cat,
        "offers": {"@type": "Offer", "url": url, "priceCurrency": "UZS",
                   "price": int(round(float(it.get("price") or 0))),
                   "availability": "https://schema.org/InStock" if instock
                   else "https://schema.org/OutOfStock",
                   "seller": {"@type": "Store", "name": "Chinor qurilish do'koni"}},
    }
    if img_abs:
        ld["image"] = img_abs
    crumbs = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Bosh sahifa", "item": BASE_URL},
            {"@type": "ListItem", "position": 2, "name": cat,
             "item": f"{BASE_URL}/b/{slug(cat)}"},
            {"@type": "ListItem", "position": 3, "name": it["name"], "item": url},
        ],
    }

    body = f"""<nav class="crumbs"><a href="/">Bosh sahifa</a> ›
<a href="/b/{slug(cat)}">{esc(cat)}</a> › {esc(it['name'])}</nav>
<h1>{esc(it['name'])}</h1>
<a class="cat" href="/b/{slug(cat)}">{esc(cat)}</a>
<div class="card">
"""
    if it["img"]:
        body += f'<div class="photo"><img src="{esc(it["img"])}" alt="{esc(it["name"])}" width="600" height="600"></div>\n'
    stock_txt = "Mavjud" if instock else "Hozircha tugagan — qo'ng'iroq qiling"
    bot_btn = ""
    if bot:
        bot_btn = ('<a class="btn alt" href="https://t.me/%s">Telegramda buyurtma</a>'
                   % esc(bot))
    body += """<div class="pad">
<div class="price">%s</div>
<div class="stock">%s · o'lchov: %s · kod: %s</div>
<div class="btns">
<a class="btn" href="tel:%s">Qo'ng'iroq qilish</a>
%s
<a class="btn alt" href="https://www.google.com/maps/dir/?api=1&amp;destination=41.2067894,69.2194187">Yo'nalish</a>
</div>
""" % (esc(price), esc(stock_txt), esc(it.get("unit") or "dona"), it["id"],
       esc(tel.replace(" ", "")), bot_btn)
    if it.get("description"):
        body += f'<p class="desc">{esc(it["description"])}</p>\n'
    if it.get("usage_info"):
        body += f'<h2>Qanday ishlatiladi</h2><p class="desc">{esc(it["usage_info"])}</p>\n'
    body += "</div></div>\n"

    if same_cat:
        body += '<h2>%s bo%slimidan yana</h2><div class="grid">\n' % (esc(cat), "'")
        for o in same_cat:
            body += tile(o)
        body += "</div>\n"

    body += ('<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False)
             + "</script>\n")
    body += ('<script type="application/ld+json">' + json.dumps(crumbs, ensure_ascii=False)
             + "</script>\n")
    page(SITE / "t" / f"{it['slug']}.html", title, desc, url, body,
         img_abs, shop, ogtype="product")
    return url


def listing_page(path, title, desc, url, heading, intro, items, shop):
    body = f'<nav class="crumbs"><a href="/">Bosh sahifa</a> › {esc(heading)}</nav>\n'
    body += '<h1>%s</h1>\n<p>%s</p>\n<div class="grid">\n' % (esc(heading), esc(intro))
    for o in items:
        body += tile(o)
    body += "</div>\n"
    page(path, title, desc, url, body, "", shop)
    return url


def main():
    print(f"▶ Katalog olinmoqda — {API}")
    items, shop = fetch_catalog()
    print(f"  {len(items)} ta tovar keldi")
    if not items:
        raise SystemExit("Tovar yo'q — to'xtadim (eski sahifalar o'chirilmadi)")

    for d in ("t", "b", "tovarlar"):
        shutil.rmtree(SITE / d, ignore_errors=True)
    img_dir = SITE / "img"
    img_dir.mkdir(parents=True, exist_ok=True)

    print("▶ Rasmlar")
    seen, clean = set(), []
    for it in items:
        s = f"{slug(it['name'])}-{it['id']}"
        if s in seen:
            continue
        seen.add(s)
        it["slug"] = s
        it["img"] = fetch_image(it.get("image") or "", img_dir)
        clean.append(it)
    items = clean
    print(f"  {sum(1 for i in items if i['img'])} / {len(items)} tasida rasm bor")

    by_cat = {}
    for it in items:
        by_cat.setdefault(it.get("category") or "Boshqa", []).append(it)

    (SITE / "assets").mkdir(parents=True, exist_ok=True)
    (SITE / "assets" / "tovar.css").write_text(CSS, encoding="utf-8")

    urls = [f"{BASE_URL}/", f"{BASE_URL}/ru/"]

    print("▶ Tovar sahifalari")
    for it in items:
        same = [o for o in by_cat[it.get("category") or "Boshqa"]
                if o["id"] != it["id"]][:8]
        urls.append(product_page(it, shop, same))

    print("▶ Bo'lim sahifalari")
    for cat, lst in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        urls.append(listing_page(
            SITE / "b" / f"{slug(cat)}.html",
            f"{cat} — narxlari | Chinor qurilish do'koni, Sergeli",
            f"{cat} bo'limi: {len(lst)} ta tovar, narxlari bilan. "
            f"Chinor qurilish do'koni, Sergeli, Chinor bekati yoni.",
            f"{BASE_URL}/b/{slug(cat)}", cat,
            f"{cat} bo'limidagi {len(lst)} ta tovar — narxi va surati bilan.",
            lst, shop))

    print("▶ Umumiy ro'yxat")
    body = '<nav class="crumbs"><a href="/">Bosh sahifa</a> › Barcha tovarlar</nav>\n'
    body += (f"<h1>Barcha tovarlar</h1><p>Do'konimizdagi {len(items)} ta tovar "
             f"surati va narxi bilan. Bo'limlar bo'yicha:</p>\n")
    for cat, lst in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        body += (f'<h2><a href="/b/{slug(cat)}">{esc(cat)}</a> '
                 f'<span style="font-weight:400;color:var(--muted)">({len(lst)})</span></h2>\n'
                 '<div class="lst">\n')
        for o in lst:
            body += f'<a href="/t/{o["slug"]}">{esc(o["name"])} — {esc(money(o.get("price")))}</a>\n'
        body += "</div>\n"
    page(SITE / "tovarlar" / "index.html", "Barcha tovarlar — Chinor qurilish do'koni, Sergeli",
         f"Chinor do'konidagi {len(items)} ta tovar: nomi, narxi, surati. "
         f"Sergeli, Chinor bekati yoni. Har kuni 8:00–22:00.",
         f"{BASE_URL}/tovarlar/", body, "", shop)
    urls.append(f"{BASE_URL}/tovarlar/")

    print("▶ sitemap.xml")
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        pr = "1.0" if u.endswith("/") and u.count("/") == 3 else "0.7"
        sm.append(f"  <url><loc>{esc(u)}</loc><lastmod>{TODAY}</lastmod>"
                  f"<changefreq>weekly</changefreq><priority>{pr}</priority></url>")
    sm.append("</urlset>")
    (SITE / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n", encoding="utf-8")

    # ── O'z-o'zini tekshirish: sitemapdagi har manzil ortida fayl turibdimi ──
    missing = []
    for u in urls:
        rel = u[len(BASE_URL):].lstrip("/")
        f = SITE / (rel + "index.html" if rel.endswith("/") or rel == "" else rel + ".html")
        if not f.exists():
            missing.append(u)
    # bosh sahifa va /ru/ generator yasamaydigan, qo'lda yozilgan sahifalar
    missing = [m for m in missing if not m.rstrip("/").endswith(("pages.dev", "chinorpos.com", "/ru"))]
    if missing:
        raise SystemExit("XATO: sahifasi yo'q manzillar: " + ", ".join(missing[:5]))

    print(f"\n✓ tayyor: {len(items)} tovar, {len(by_cat)} bo'lim, "
          f"{len(urls)} manzil sitemapda")
    print(f"  manzil: {BASE_URL}  (o'zgartirish: BASE_URL)")
    print("  chiqarish: bash deploy/site-deploy.sh")


if __name__ == "__main__":
    sys.exit(main())
