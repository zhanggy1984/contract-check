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
pages = OcrService.ocr_pdf(pdf)   # {页索引: 文本}
dt = time.time() - t0
print(f"OCR 耗时 {dt:.1f}s，识别 {len(pages)} 页")
print("--- 识别结果 ---")
for idx, text in sorted(pages.items()):
    print(f"[第 {idx} 页]")
    for line in text.splitlines()[:10]:
        print(line)
