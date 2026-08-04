"""Katalog va qoldiqni gateway'dan tortib, o'z bazamizga yozadi.

Ishlatish:
    python -m marketplace.sync catalog    # kategoriya + tovar ro'yxati
    python -m marketplace.sync stock      # kunlik qoldiq surati
    python -m marketplace.sync all        # ikkalasi
    python -m marketplace.sync status     # baza holati

Kunlik cron (Toshkent vaqti bilan har kuni 23:30):
    30 23 * * *  cd /opt/chinor && python -m marketplace.sync all >> data/sync.log 2>&1
"""

import argparse
import logging
import sys

from marketplace import config
from marketplace.client import YesposClient, YesposError
from marketplace.db import MarketplaceDB, today_local

log = logging.getLogger("marketplace.sync")


def sync_catalog(client: YesposClient, db: MarketplaceDB) -> int:
    """Kategoriya + tovar ro'yxatini yangilaydi. Qaytaradi: tovar soni."""
    run_id = db.start_run("catalog")
    try:
        categories = client.products()
        products = []
        for cat in categories:
            for p in cat.get("products") or []:
                # Kategoriya ba'zan tovar ichida bo'lmasligi mumkin — to'ldiramiz.
                p.setdefault("category", cat.get("id"))
                products.append(p)

        db.upsert_categories(categories)
        db.upsert_products(products)
        db.finish_run(run_id, ok=True, items=len(products))
        log.info("katalog: %d kategoriya, %d tovar", len(categories), len(products))
        return len(products)
    except Exception as e:
        db.finish_run(run_id, ok=False, error=str(e))
        raise


def sync_stock(client: YesposClient, db: MarketplaceDB) -> int:
    """Bugungi narx/qoldiq suratini yozadi. Qaytaradi: yozuvlar soni."""
    run_id = db.start_run("stock")
    try:
        rows = list(client.iter_products_info())
        n = db.save_stock_snapshot(rows)
        db.finish_run(run_id, ok=True, items=n)
        log.info("qoldiq: %s uchun %d yozuv", today_local(), n)
        return n
    except Exception as e:
        db.finish_run(run_id, ok=False, error=str(e))
        raise


def show_status(db: MarketplaceDB) -> None:
    c = db.conn
    products = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
    cats = c.execute("SELECT COUNT(*) n FROM categories").fetchone()["n"]
    days = db.snapshot_days()
    rng = c.execute("SELECT MIN(day) a, MAX(day) b FROM stock_daily").fetchone()
    no_img = len(db.products_without_image())

    print(f"Baza:        {config.DB_PATH}")
    print(f"Filial:      {config.BRANCH}")
    print(f"Kategoriya:  {cats}")
    print(f"Tovar:       {products}  (rasmi yo'q: {no_img})")
    print(f"Qoldiq kuni: {days}" + (f"  ({rng['a']} … {rng['b']})" if rng["a"] else ""))

    if days < config.OUT_OF_STOCK_DAYS:
        print(
            f"\nAnalitika uchun kamida {config.OUT_OF_STOCK_DAYS} kunlik surat kerak "
            f"(muzlagan tovar uchun {config.DEAD_STOCK_DAYS}). "
            f"Hozircha {days} kun — har kuni `sync stock` ishlab tursin."
        )

    last = c.execute(
        "SELECT kind, started_at, ok, items, error FROM sync_runs ORDER BY id DESC LIMIT 5"
    ).fetchall()
    if last:
        print("\nOxirgi sinxronlar:")
        for r in last:
            mark = "✓" if r["ok"] else "✗"
            tail = f" — {r['error']}" if r["error"] else ""
            print(f"  {mark} {r['started_at']}  {r['kind']:<8} {r['items']:>6}{tail}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="YesPOS marketplace sinxroni")
    ap.add_argument("command", choices=["catalog", "stock", "all", "status"])
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    with MarketplaceDB() as db:
        if args.command == "status":
            show_status(db)
            return 0

        try:
            client = YesposClient()
            if args.command in ("catalog", "all"):
                n = sync_catalog(client, db)
                if n == 0:
                    log.warning(
                        "Gateway 0 ta tovar qaytardi — YesPOS tomonida tovarlar "
                        "marketplace uchun yoqilmagan bo'lishi mumkin."
                    )
            if args.command in ("stock", "all"):
                sync_stock(client, db)
        except YesposError as e:
            log.error("%s", e)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
