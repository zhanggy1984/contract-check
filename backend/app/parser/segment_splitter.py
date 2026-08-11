"""合同文本分段（T1.4 总是运行；Phase 3 语义校验按 segment_ref 定位）。

按章节标题（第X条 / 一、二、三）切片，产出 [{index, title, content}]。
无章节标记的短合同 → 整文单段。
"""
import re

# 章节标题：第X条 / 第X章 / 一、二、三、（顶格数字）
_HEADER = re.compile(
    r"^\s*(?:(?:第[一二三四五六七八九十百千万0-9０-９]+[条章节])|"
    r"(?:[一二三四五六七八九十]+[、．])|(?:\d+[、．]))\s*(.*?)\s*$"
)
# 标题 token：仅取"第X条"这类头部（不含行内正文）
_HEADING_TOKEN = re.compile(r"^(第[一二三四五六七八九十百千万0-9０-９]+[条章节])")


def _heading_of(line: str) -> str:
    """标题行的简短标题：优先"第X条"，其次"一、/1、"序号，兜底取行首。"""
    s = line.strip()
    m = _HEADING_TOKEN.match(s)
    if m:
        return m.group(1)
    m = re.match(r"^([一二三四五六七八九十百千万零]+[、．]|\d+[、．])", s)
    return m.group(1) if m else s[:20]


def split_segments(text: str, max_chars: int = 20000) -> list[dict]:
    """按章节切片。单段超 max_chars 时按段落再切分，保证每段可控。

    修复（T4.6）：标题与正文同在一行时（如"第一条 合同标的：……"），行内正文必须归属
    该标题段。原实现把行内正文只塞进 title、连续标题行时又被下一行覆盖，导致条款正文整段
    丢失（segments 只剩标题和盖章，语义评估看不到违约/付款等条款 → 误报）。
    """
    text = (text or "").strip()
    if not text:
        return []
    lines = text.split("\n")
    segments: list[dict] = []
    cur_title, cur_lines = None, []
    for line in lines:
        m = _HEADER.match(line)
        if m:
            rest = (m.group(1) or "").strip()
            if cur_lines:
                segments.append(_flush(cur_title, cur_lines))
            cur_title = _heading_of(line)
            cur_lines = [rest] if rest else []
        else:
            cur_lines.append(line)
    if cur_lines:
        segments.append(_flush(cur_title, cur_lines))
    if not segments:
        segments = [_flush(None, lines)]
    # 超长段硬切（极少数无段落长文）
    out: list[dict] = []
    for seg in segments:
        content = seg["content"]
        if len(content) > max_chars:
            for i in range(0, len(content), max_chars):
                out.append({"index": len(out), "title": seg["title"], "content": content[i:i + max_chars]})
        else:
            out.append(seg)
    # 重排 index
    for i, seg in enumerate(out):
        seg["index"] = i
    return out


def _flush(title: str | None, lines: list[str]) -> dict:
    content = "\n".join(lines).strip()
    return {"index": 0, "title": title or "", "content": content}
