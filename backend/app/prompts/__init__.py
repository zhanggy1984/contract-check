"""提示词模板集中存放与加载。

每个提示词一个纯文本文件（.md），正文即全部内容、零转义——改文案不必动代码。

**为什么只有 system prompt 在这里**：本目录 4 个模板都是纯文案、**不跑任何插值**，
模块内 `SYSTEM_PROMPT = load_prompt("x")` 即可，调用方与既有测试无需改动。
（注：`semantic_system.md` 含一对字面大括号——JSON 示例——因为 `load_prompt` 原样返回、
不跑 `format`/`Template`，故零影响。日后若给模板加插值，须先处理这处大括号。）
用户消息模板**不在此列**——它们由 f-string 现场拼装，且 _build_prompt 内部
还在循环遍历规则列表、调用 guard_text()/json.dumps()，属于「模板 + 逻辑」的
混合体而非文案；外置它要么留着 f-string（等于没外置），要么把循环也搬出去
（那文件就不是模板而是脚本了）。

放在包目录内，随 Dockerfile 的 `COPY app ./app` 进镜像，无需改 Dockerfile。
"""
from pathlib import Path

_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """读取提示词模板正文。

    Args:
        name: 模板名（不含 .md 后缀），如 "extractor_system"。

    Returns:
        模板正文，原样返回、不做任何插值。
    """
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")
