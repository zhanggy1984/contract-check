# 后端镜像（T4.5）：python:3.11-slim + PaddleOCR + 开源中文字体
FROM python:3.11-slim

WORKDIR /app

# 系统依赖：paddlepaddle 需 libgomp1/libstdc++，opencv 需 libglib2.0/libgl1；gosu 供非 root 降权
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libstdc++6 libglib2.0-0 libgl1 gosu \
    && rm -rf /var/lib/apt/lists/*

# 依赖层（分层缓存）：国内镜像加速，装完整 requirements（含 OCR 全家桶）
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# 应用代码 + 规则 + 中文字体
COPY app ./app
COPY ontology ./ontology
COPY rules ./rules
COPY fonts ./fonts

# 报告中文字体（T4.5：bundle 开源字体，font.py 的 REPORT_FONT 优先探测）
ENV REPORT_FONT=/app/fonts/NotoSansSC-VF.ttf
ENV PYTHONIOENCODING=utf-8

# 数据目录 + 非 root 用户（S1）：应用进程以 appuser 运行
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/data/uploads /app/data/parsed \
    && chown -R appuser:appuser /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
