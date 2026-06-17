"""Shtrix-kod (barcode) rasmidan kodni o'qib oladi.

Kutubxonalar (ixtiyoriy, mavjud bo'lsa ishlatiladi):
    • pyzbar + Pillow  — EAN, UPC, Code-128 va boshqalar uchun
    • opencv-python    — barcode + QR uchun zaxira

Ikkalasi ham bo'lmasa — `decode_barcode` `None` qaytaradi va foydalanuvchidan
shtrix-kod matnini qo'lda kiritish so'raladi.
"""

import io
from typing import Optional


def decode_barcode(image_bytes: bytes) -> Optional[str]:
    """Berilgan rasm baytlaridan shtrix-kod matnini qaytaradi.
    Birinchi topilgan kodning matnini qaytaradi, hech narsa topilmasa — None."""
    if not image_bytes:
        return None

    # 1) pyzbar — eng ishonchli (zbar kutubxonasi orqali)
    try:
        from pyzbar.pyzbar import decode as zbar_decode  # type: ignore
        from PIL import Image  # type: ignore
        img = Image.open(io.BytesIO(image_bytes))
        for r in zbar_decode(img):
            data = r.data
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="ignore")
            data = (data or "").strip()
            if data:
                return data
    except Exception:
        pass

    # 2) OpenCV — zaxira variant
    try:
        import numpy as np  # type: ignore
        import cv2  # type: ignore
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        # 2a) BarcodeDetector (OpenCV 4.8+)
        try:
            detector = cv2.barcode.BarcodeDetector()
            ok, decoded_info, _, _ = detector.detectAndDecodeMulti(img)
            if ok:
                for s in decoded_info or []:
                    s = (s or "").strip()
                    if s:
                        return s
        except Exception:
            pass

        # 2b) QRCodeDetector
        try:
            qr = cv2.QRCodeDetector()
            data, _, _ = qr.detectAndDecode(img)
            data = (data or "").strip()
            if data:
                return data
        except Exception:
            pass
    except Exception:
        pass

    return None


def is_available() -> bool:
    """Tizimda shtrix-kod o'qiy oladigan kutubxona bormi?"""
    try:
        from pyzbar.pyzbar import decode  # type: ignore  # noqa: F401
        return True
    except Exception:
        pass
    try:
        import cv2  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False
