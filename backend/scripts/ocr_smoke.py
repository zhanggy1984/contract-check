"""OCR 冒烟测试（T4.1）：对扫描测试 PDF 跑 OcrService，打印识别结果与前 N 行。

用法：python scripts/ocr_smoke.py [pdf_path]
"""
import sys
import time

sys.path.insert(0, ".")

from app.ocr.ocr_service import OcrService  # noqa: E402

pdf = sys.argv[1] if len(sys.argv) > 1 else "data/scanned_test.pdf"

print("OcrService.available():", OcrService.available())
t0 = time.time()
text = OcrService.ocr_pdf(pdf)
dt = time.time() - t0
print(f"OCR 耗时 {dt:.1f}s，识别 {len(text)} 字符")
print("--- 识别结果 ---")
for line in text.splitlines()[:30]:
    print(line)
