"""合同文本分段（T1.4 总是运行；Phase 3 语义校验按 segment_ref 定位）。

按章节标题（第X条 / 一、二、三）切片，产出 [{index, title, content}]。
无章节标记的短合同 → 整文单段。
条款内枚举子项不拆段：阿拉伯数字编号（"1、交付服务器壹台"）一律视为子项并入当前段
（合同正文中几乎总是枚举而非章节）；中文编号（"一、总则"）仅在行内正文很短时
（≤ _SUBITEM_MAX_TITLE 字符）才视为章节标题，长正文同样是子项。
防拆碎依据：语义评估按段批跑，子项被拆成独立段后条款上下文割裂 → 误判。
"""
import re

# 章节标题（无条件）：第X条 / 第X章 / 第X节
_HEADER_CLAUSE = re.compile(r"^\s*(第[一二三四五六七八九十百千万0-9０-９]+[条章节])\s*(.*?)\s*$")
# 中文编号标题（条件）：一、二、三 —— 仅行内正文很短时算标题
_HEADER_CN = re.compile(r"^\s*([一二三四五六七八九十]+[、．])\s*(.*?)\s*$")
# 中文编号行行内正文超过该长度即视为条款子项（"一、交付服务器壹台、交换机两台"），不拆段
_SUBITEM_MAX_TITLE = 20


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
        m = _HEADER_CLAUSE.match(line)
        if m:
            # 第X条：无条件章节标题，行内正文归属该段（T4.6）
            rest = (m.group(2) or "").strip()
            if cur_lines:
                segments.append(_flush(cur_title, cur_lines))
            cur_title = m.group(1)
            cur_lines = [rest] if rest else []
            continue
        m = _HEADER_CN.match(line)
        if m and len((m.group(2) or "").strip()) <= _SUBITEM_MAX_TITLE:
            # 中文编号行且行内正文很短：视为章节标题（"一、总则"）
            rest = (m.group(2) or "").strip()
            if cur_lines:
                segments.append(_flush(cur_title, cur_lines))
            cur_title = m.group(1)
            cur_lines = [rest] if rest else []
            continue
        # 普通行 / 阿拉伯数字编号（1、2、3）/ 长正文中文编号：均为条款子项或正文，
        # 并入当前段，防枚举子项拆碎条款上下文
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
