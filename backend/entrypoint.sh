#!/bin/sh
set -e
# S1：应用进程以非 root 运行。entrypoint 以 root 启动（需要 chown 卷），
# 然后 gosu 降权到 appuser 执行 uvicorn。
chown -R appuser:appuser /app/data/uploads /app/data/parsed 2>/dev/null || true
exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8000
