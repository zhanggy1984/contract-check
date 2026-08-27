"""PaddleOCR 服务（T4.1）：扫描型 PDF → 文本。

- 惰性加载：首次调用才加载模型（数百 MB），避免应用启动变慢
- 置信度阈值：低于阈值的结果丢弃，防"垃圾进垃圾出"
- 页级隔离：pages 指定时只识别这些页（混合扫描 PDF 逐页 OCR），单页失败不中止整篇
- 失败降级：OCR 不可用/全部页失败抛明确异常，由图节点置任务 FAILED 提示人工
- 兼容 PaddleOCR 2.x（ocr()）与 3.x（predict()）返回结构
"""
import logging
import threading

import numpy as np
import pymupdf

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6  # 低于该置信度的识别行丢弃
_LOW_CONF = 0.8  # 已识别行低于该置信度计"低质量"（低置信行占比统计用）

_ocr = None
_lock = threading.Lock()


class OcrService:
    """无状态静态服务；模型为进程内惰性单例（仅首次调用加载）。"""

    @staticmethod
    def available() -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except ImportError:
            return False

    @classmethod
    def _model(cls):
        global _ocr
        if _ocr is None:
            with _lock:
                if _ocr is None:
                    from paddleocr import PaddleOCR
                    # enable_mkldnn=False：绕开 paddlepaddle 3.x oneDNN+PIR 在 CPU 上的
                    # ConvertPirAttribute2RuntimeAttribute 崩溃（Windows 实测必现）
                    _ocr = PaddleOCR(lang="ch", enable_mkldnn=False)
        return _ocr

    @classmethod
    def ocr_pdf(cls, pdf_path: str, pages: list[int] | None = None) -> dict[int, str]:
        """扫描 PDF 页 → OCR 文本，返回 {页索引: 文本}（质量统计见 ocr_pdf_with_stats）。"""
        results, _ = cls.ocr_pdf_with_stats(pdf_path, pages=pages)
        return results

    @classmethod
    def ocr_pdf_with_stats(cls, pdf_path: str, pages: list[int] | None = None) -> tuple[dict[int, str], dict]:
        """扫描 PDF 页 → OCR 文本 + 识别质量指标（T4.3-9）。

        统计供观测/标记低质量扫描件：coverage=成功识别页/应识别页，avg_confidence=阈值以上
        行平均置信度，low_conf_line_ratio=低置信度(<0.8)行占比——低质量扫描件这两项显著走低，
        可用于日志告警或后续质量标记（本期仅统计+日志，不落库）。

        pages=None 时全部页；指定 pages 时只识别这些页（混合扫描 PDF 仅 OCR 空页）。
        逐页隔离：单页渲染/识别失败不中止整篇（该页缺席，调用方感知缺页）；
        全部页失败抛 RuntimeError（图节点置任务 FAILED 提示人工，不做空文本进抽取）。
        """
        if not cls.available():
            raise RuntimeError("PaddleOCR 未安装，无法识别扫描件")
        model = cls._model()

        doc = pymupdf.open(pdf_path)
        indices = list(range(len(doc))) if pages is None else sorted(set(pages))
        results: dict[int, str] = {}
        failures = 0
        scores: list[float] = []
        low_conf = 0
        lines_total = 0
        chars_total = 0
        try:
            for i in indices:
                try:
                    page = doc[i]
                    pix = page.get_pixmap(dpi=200)
                    img = np.frombuffer(pix.samples, dtype=np.uint8)
                    img = img.reshape(pix.height, pix.width, pix.n)
                    if pix.n == 4:
                        img = img[:, :, :3]
                    page_lines: list[str] = []
                    for text, score in cls._run(model, img):
                        s = float(score)
                        if s >= CONFIDENCE_THRESHOLD:
                            t = str(text).strip()
                            if t:
                                page_lines.append(t)
                                lines_total += 1
                                chars_total += len(t)
                                scores.append(s)
                                if s < _LOW_CONF:
                                    low_conf += 1
                    if page_lines:
                        results[i] = "\n".join(page_lines)
                except Exception as e:  # noqa: BLE001 单页失败不中止整篇
                    failures += 1
                    logger.warning("OCR 失败 第 %s 页: %s", i, e)
        finally:
            doc.close()
        if indices and failures == len(indices):
            raise RuntimeError(f"OCR 全部失败（{failures}/{len(indices)} 页）")
        pages_total = len(indices)
        pages_ok = len(results)
        stats = {
            "pages_total": pages_total,
            "pages_ok": pages_ok,
            "coverage": (pages_ok / pages_total) if pages_total else 0.0,
            "lines_total": lines_total,
            "chars_total": chars_total,
            "avg_confidence": (sum(scores) / len(scores)) if scores else 0.0,
            "low_conf_line_ratio": (low_conf / lines_total) if lines_total else 0.0,
        }
        logger.info("OCR 质量统计: 覆盖 %d/%d 页(%.0f%%), %d 行 %d 字符, "
                    "平均置信度 %.2f, 低置信行占比 %.0f%%",
                    pages_ok, pages_total, stats["coverage"] * 100,
                    lines_total, chars_total, stats["avg_confidence"],
                    stats["low_conf_line_ratio"] * 100)
        return results, stats

    @staticmethod
    def _run(model, img) -> list[tuple[str, float]]:
        """归一化为 [(text, score)]，兼容 2.x ocr() 与 3.x predict()。"""
        if hasattr(model, "predict"):  # PaddleOCR 3.x
            out: list[tuple[str, float]] = []
            for item in model.predict(img):
                out.extend(zip(item.get("rec_texts", []), item.get("rec_scores", [])))
            return out
        # PaddleOCR 2.x：ocr(img) → [[[box, (text, score)], ...], ...]
        out = []
        for page in (model.ocr(img, cls=True) or []):
            for row in (page or []):
                if row and row[1]:
                    out.append((row[1][0], row[1][1]))
        return out
