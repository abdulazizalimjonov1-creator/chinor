"""Serverda sennik chizilyaptimi — botga tegmasdan tekshiradi.

Nima uchun: sennik serverda rasmga chiziladi, xato esa faqat tugma
bosilganda bilinadi. Bu skript o'sha yo'lni botdan tashqarida yuradi —
deploy'dan keyin yangi modul ko'chirilmay qolgan bo'lsa, shu yerda
ko'rinadi (2026-10-29 da aynan shunday bo'lgan: ImportError).

    C:\chinor\.venv\Scripts\python.exe C:\ChinorSennik\selftest.py
"""
import os, sys
sys.path.insert(0, r"C:\chinor\chinor-bot")
os.environ.setdefault("DATA_DIR", r"C:\chinor\data")
from bot import print_agent, barcode_gen
lb = {"name": "Sinov tovari", "price": 12345, "sku": 10011, "unit": "dona", "barcode": ""}
code, kind = barcode_gen.for_product(lb["barcode"], lb["sku"])
data = print_agent.tspl_label(lb, "30x20")
print("OK | kod:", code, kind, "| TSPL bayt:", len(data))
