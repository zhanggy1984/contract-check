#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地 mock LLM 端点：模拟 LLM 故障两种行为，用于 e2e 验证语义降级路径。

后端把 DEEPSEEK_BASE_URL 指向本 mock 时（backend 容器经 host.docker.internal 访问）：

- MOCK_MODE=semantic_degrade：抽取调用代理到真实 DeepSeek（下游确定性校验跑真实抽取数据），
  语义/决策调用返回 500 → 语义整体降级（全 SKIPPED/LOW）→ 任务进 WAITING_REVIEW（人工确认）
  而非静默 SUCCESS。注意：仅代理抽取，不代表"抽取也故障"。
- MOCK_MODE=total_fail：全部调用返回 500 → 抽取失败 → 任务 FAILED（可复现，非静默成功）。
- MOCK_MODE=proxy（默认）：全量透传真实 DeepSeek（冒烟用）。

行为区分依据（服务端真实标记，非猜测）：system 提示词——
抽取 SYSTEM_PROMPT 含 "抽取为结构化 JSON"；语义 SYSTEM_PROMPT 含 "逐条判定"；
决策通道走 tools 参数（body 含 tools 数组）。

真实凭证与上游地址从 backend/.env 读取。仅限本地验收走查使用。
"""
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = "0.0.0.0", 8123

# 通道标记：system 提示词中区分抽取/语义的关键子串（与 extractor/semantic_evaluator 的
# SYSTEM_PROMPT 对齐；改动 prompt 需同步此处）
EXTRACT_MARK = "抽取为结构化 JSON"
SEMANTIC_MARK = "逐条判定"

logger = logging.getLogger("mock_llm")

MODE = "proxy"
API_KEY = ""
REAL_BASE = "https://api.deepseek.com"


def _load_env(path: str) -> dict:
    """读 .env：KEY=VALUE 行 → dict（忽略注释/空行/行内注释）。"""
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.split("#")[0].strip().strip('"').strip("'")  # 剥离行内注释
            env[k.strip()] = v
    return env


def _system_text(payload: dict) -> str:
    """从请求体抽取全部 system 消息拼接（提取/语义 prompt 都在 messages 里）。"""
    parts = []
    for m in payload.get("messages", []):
        if m.get("role") == "system":
            c = m.get("content", "")
            if isinstance(c, list):  # 个别情况 content 为块列表
                c = "".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
            parts.append(str(c))
    return "\n".join(parts)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        logger.info("%s %s", self.command, fmt % args)

    # ---- 响应工具 ----
    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str, errtype: str = "server_error"):
        self._json(code, {"error": {"message": message, "type": errtype,
                                    "code": f"mock_{errtype}"}})

    def _proxy(self, body: bytes):
        """透传原始请求到真实 DeepSeek，上游响应原样返回（SDK 按真实响应解析）。"""
        url = REAL_BASE.rstrip("/") + "/chat/completions"
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {API_KEY}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type",
                                 resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    # ---- 路由 ----
    def do_GET(self):
        if self.path.rstrip("/") == "/healthz":
            self._json(200, {"status": "ok", "mode": MODE})
        else:
            self._error(404, f"not found: {self.path}", "not_found")

    def do_POST(self):
        if self.path.rstrip("/") != "/chat/completions":
            self._error(404, f"not found: {self.path}", "not_found")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception as e:  # noqa: BLE001 非 JSON 请求体 → 客户端错误
            self._error(400, f"bad json: {e}", "invalid_request_error")
            return
        system = _system_text(payload)
        is_extract = EXTRACT_MARK in system
        is_semantic = SEMANTIC_MARK in system
        channel = "extract" if is_extract else (
            "semantic" if is_semantic else ("decision" if payload.get("tools") else "other"))
        logger.info("[mock] channel=%s mode=%s", channel, MODE)

        if MODE == "total_fail":
            self._error(500, f"mock total_fail: {channel} endpoint unavailable")
            return
        if MODE == "semantic_degrade" and not is_extract:
            self._error(500, f"mock semantic_degrade: {channel} endpoint unavailable")
            return
        # proxy 模式 & semantic_degrade 的抽取分支 → 透传真实 DeepSeek
        self._proxy(body)


def main():
    global MODE, API_KEY, REAL_BASE
    MODE = os.environ.get("MOCK_MODE", "proxy").strip().lower()
    if MODE not in ("semantic_degrade", "total_fail", "proxy"):
        sys.exit(f"未知 MOCK_MODE={MODE}（可选 semantic_degrade|total_fail|proxy）")
    backend_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    env = _load_env(backend_env)
    API_KEY = os.environ.get("MOCK_API_KEY") or env.get("DEEPSEEK_API_KEY", "")
    # 注意：真实上游地址不读 backend/.env 的 DEEPSEEK_BASE_URL——走查时它会被临时改指向
    # mock 自身，读了会形成自代理死循环。固定默认 https://api.deepseek.com，可用
    # MOCK_REAL_BASE 环境变量覆盖（换供应商/网关时用）。
    REAL_BASE = os.environ.get("MOCK_REAL_BASE") or "https://api.deepseek.com"
    if not API_KEY:
        sys.exit("未找到 DEEPSEEK_API_KEY（backend/.env 或 MOCK_API_KEY 环境变量）")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("mock LLM 启动：host=%s port=%s mode=%s proxy=%s", HOST, PORT, MODE, REAL_BASE)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
