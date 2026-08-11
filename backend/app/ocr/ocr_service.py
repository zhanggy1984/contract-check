"""PaddleOCR 服务（T4.1）：扫描型 PDF → 文本。

- 惰性加载：首次调用才加载模型（数百 MB），避免应用启动变慢
- 置信度阈值：低于阈值的结果丢弃，防"垃圾进垃圾出"
- 失败降级：OCR 不可用/失败抛明确异常，由图节点置任务 FAILED 提示人工
- 兼容 PaddleOCR 2.x（ocr()）与 3.x（predict()）返回结构
"""
import threading

CONFIDENCE_THRESHOLD = 0.6  # 低于该置信度的识别行丢弃

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
    def ocr_pdf(cls, pdf_path: str) -> str:
        """扫描 PDF 逐页渲染为位图 → OCR → 按行拼接文本（低置信行丢弃）。"""
        if not cls.available():
            raise RuntimeError("PaddleOCR 未安装，无法识别扫描件")
        model = cls._model()
        import numpy as np
        import pymupdf

        doc = pymupdf.open(pdf_path)
        lines: list[str] = []
        try:
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                img = np.frombuffer(pix.samples, dtype=np.uint8)
                img = img.reshape(pix.height, pix.width, pix.n)
                if pix.n == 4:
                    img = img[:, :, :3]
                for text, score in cls._run(model, img):
                    if float(score) >= CONFIDENCE_THRESHOLD:
                        t = str(text).strip()
                        if t:
                            lines.append(t)
        finally:
            doc.close()
        return "\n".join(lines)

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
