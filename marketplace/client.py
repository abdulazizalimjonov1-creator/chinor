"""YesPOS marketplace gateway klienti.

Gateway hujjati: https://github.com/yestask91-byte/marketplace

Muhim jihatlar (hujjatdan va real testdan):
  • `page`/`limit` — QUERY param, body emas. Body ga solinsa server 500 beradi.
  • Har endpointning o'z limiti bor (products 15/daqiqa, info 10/daqiqa,
    branch 15/daqiqa, order 1/sekund). Klient limitni o'zi hisoblab kutadi.
  • Limitga urilganda server `{"error": "... Qolgan vaqt: 19s"}` qaytaradi —
    o'sha sekundni o'qib, shuncha kutib qayta uriniladi.
  • Muvaffaqiyat: `{"code": 0, "message": "OK", "data": ...}`.
"""

import re
import time
import logging
from collections import deque
from typing import Any, Dict, Iterator, List, Optional

import requests

from marketplace import config

log = logging.getLogger(__name__)

# Endpoint → (nechta so'rov, necha sekund oynada). Hujjatdagi limitlar.
_LIMITS = {
    "marketplace/products": (15, 60),
    "marketplace/products/info": (10, 60),
    "marketplace/order": (1, 1),
    "branch/list": (15, 60),
}

# Xato matnidan qolgan kutish vaqtini ajratib olish: "... Qolgan vaqt: 19s"
_WAIT_RE = re.compile(r"(\d+)\s*s")


class YesposError(RuntimeError):
    """Gateway `{"error": ...}` qaytarganda ko'tariladi."""


class _RateLimiter:
    """Oddiy siljiydigan oyna (sliding window) — endpoint bo'yicha alohida."""

    def __init__(self):
        self._hits: Dict[str, deque] = {}

    def wait(self, path: str) -> None:
        limit, window = _LIMITS.get(path, (0, 0))
        if not limit:
            return
        q = self._hits.setdefault(path, deque())
        now = time.monotonic()
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            sleep_for = window - (now - q[0]) + 0.05
            if sleep_for > 0:
                log.debug("limit: %s uchun %.1fs kutilmoqda", path, sleep_for)
                time.sleep(sleep_for)
            return self.wait(path)
        q.append(time.monotonic())


class YesposClient:
    def __init__(
        self,
        base_url: str = config.BASE_URL,
        api_key: str = config.API_KEY,
        branch: str = config.BRANCH,
        app_name: str = config.APP_NAME,
        timeout: int = config.TIMEOUT,
        retries: int = config.RETRIES,
    ):
        if not api_key:
            raise YesposError(
                "YESPOS_API_KEY bo'sh. .env fayliga kalitni qo'shing "
                "(namuna: marketplace/.env.example)."
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.branch = str(branch)
        self.app_name = app_name
        self.timeout = timeout
        self.retries = max(1, retries)
        self._limiter = _RateLimiter()
        self._session = requests.Session()

    # ── Ichki so'rov yuborgich ────────────────────────────────────────────

    def _post(
        self,
        path: str,
        body: Optional[dict] = None,
        params: Optional[dict] = None,
        extra_headers: Optional[dict] = None,
    ) -> Any:
        headers = {"API-Key": self.api_key, "Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        url = f"{self.base_url}/{path}"

        last_err: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            self._limiter.wait(path)
            try:
                r = self._session.post(
                    url,
                    json=body if body is not None else {},
                    params=params or None,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_err = e
                log.warning("%s — tarmoq xatosi (%d/%d): %s", path, attempt, self.retries, e)
                time.sleep(min(2 ** attempt, 15))
                continue

            # Gateway ba'zan nginx'ning HTML xato sahifasini qaytaradi.
            try:
                data = r.json()
            except ValueError:
                last_err = YesposError(f"{path}: JSON emas (HTTP {r.status_code})")
                if r.status_code >= 500 and attempt < self.retries:
                    time.sleep(min(2 ** attempt, 15))
                    continue
                raise last_err

            if isinstance(data, dict) and "error" in data:
                msg = str(data["error"])
                # Gateway ba'zan izohni alohida maydonda beradi
                # (masalan {"error": -8, "error_note": "Error in request from client"}).
                if data.get("error_note"):
                    msg = f"{msg} — {data['error_note']}"
                # Limitga urildik — server aytgan sekundni kutib qayta urinamiz.
                if "so‘rov" in msg or "so'rov" in msg:
                    m = _WAIT_RE.search(msg)
                    pause = int(m.group(1)) + 1 if m else 5
                    log.info("%s — limit, %ds kutilmoqda", path, pause)
                    time.sleep(pause)
                    continue
                raise YesposError(f"{path}: {msg}")

            if isinstance(data, dict) and data.get("code") == 0:
                return data
            raise YesposError(f"{path}: kutilmagan javob: {str(data)[:200]}")

        raise YesposError(f"{path}: {self.retries} urinish natijasiz — {last_err}")

    # ── Ommaviy metodlar ──────────────────────────────────────────────────

    def branches(self) -> List[dict]:
        """Filiallar ro'yxati."""
        return self._post("branch/list").get("data") or []

    def products(self, product_ids: Optional[List[int]] = None) -> List[dict]:
        """Katalog — kategoriyalar ro'yxati, har birida `products` massivi.

        Gateway pagination ochmaydi: butun katalog bitta so'rovda keladi.
        """
        body = {"product_ids": product_ids} if product_ids else {}
        return self._post("marketplace/products", body).get("data") or []

    def products_info(
        self,
        product_ids: Optional[List[int]] = None,
        page: int = 1,
        limit: int = 500,
    ) -> dict:
        """Bir sahifa narx/qoldiq. To'liq javobni (pagination bilan) qaytaradi."""
        body = {"product_ids": product_ids} if product_ids else {}
        return self._post(
            "marketplace/products/info",
            body,
            params={"page": page, "limit": limit},
            extra_headers={"Branch": self.branch},
        )

    def iter_products_info(
        self, product_ids: Optional[List[int]] = None, limit: int = 500
    ) -> Iterator[dict]:
        """Barcha sahifalarni aylanib, har bir tovar yozuvini beradi.

        `limit` maksimumi 500 (hujjat talabi).
        """
        limit = min(max(int(limit), 1), 500)
        page = 1
        while True:
            resp = self.products_info(product_ids, page=page, limit=limit)
            rows = resp.get("data") or []
            for row in rows:
                yield row
            pg = resp.get("pagination") or {}
            total_pages = int(pg.get("total_pages") or 0)
            if page >= total_pages or not rows:
                return
            page += 1

    def create_order(
        self,
        items: List[dict],
        payments: List[dict],
        type_: int = 1,
        note: str = "",
        delivery_info: Optional[dict] = None,
    ) -> dict:
        """Buyurtma (`type_=1`) yoki qaytarish (`type_=2`) yaratadi.

        `items`   — [{"item": product_id, "qty": n, "price": narx}, ...]
        `payments`— [{"payment_id": id, "value": summa}, ...]

        Gateway talabi: items yig'indisi payments yig'indisiga teng bo'lishi shart.
        Shuning uchun yuborishdan oldin shu yerda tekshiriladi.
        """
        items_total = sum(float(i["qty"]) * float(i["price"]) for i in items)
        pay_total = sum(float(p["value"]) for p in payments)
        if abs(items_total - pay_total) > 0.01:
            raise YesposError(
                f"items yig'indisi ({items_total:,.0f}) payments yig'indisiga "
                f"({pay_total:,.0f}) teng emas — gateway rad etadi."
            )
        body = {
            "type": int(type_),
            "note": note,
            "delivery_info": delivery_info,
            "items": items,
            "payments": payments,
        }
        return self._post(
            "marketplace/order", body, extra_headers={"AppName": self.app_name}
        )
