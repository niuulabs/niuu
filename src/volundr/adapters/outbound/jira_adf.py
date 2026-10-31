"""Render Atlassian Document Format (ADF) to Markdown.

Jira Cloud REST API v3 returns rich-text fields such as an issue's
``description`` as an ADF JSON tree. Agents and UIs read issue bodies as
text, so the Jira adapter renders ADF to Markdown that preserves structure
(headings, lists, task lists, code blocks, tables, links).

Node types without a dedicated rendering keep their text: the renderer
descends into their ``content`` (or uses ``attrs.text`` for leaf nodes), so
no wording from the ticket is dropped.
"""

from __future__ import annotations

from typing import Any

_BULLET = "- "


def text_to_adf(text: str) -> dict[str, Any]:
    """Wrap plain text (or Markdown source) as an ADF document, one paragraph per line.

    Jira API v3 accepts rich-text fields only as ADF. The text is kept
    verbatim, so Markdown syntax stays readable and round-trips through
    :func:`adf_to_markdown` unchanged.
    """
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}] if line else []}
        for line in text.splitlines() or [""]
    ]
    return {"type": "doc", "version": 1, "content": paragraphs}


def adf_to_markdown(value: Any) -> str | None:
    """Render an ADF document (or a plain string) to Markdown.

    Returns ``None`` when the field is empty — the issue has no body.
    Plain strings (Jira Server / API v2 responses) are returned unchanged.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        raise TypeError(f"Jira rich-text field is not ADF: {type(value).__name__}")
    rendered = _render_blocks(value.get("content") or []).strip("\n")
    return rendered or None


def _render_blocks(nodes: list[dict[str, Any]], *, separator: str = "\n\n") -> str:
    parts = [_render_block(node) for node in nodes]
    return separator.join(part for part in parts if part)


def _render_block(node: dict[str, Any]) -> str:
    node_type = node.get("type")
    attrs = node.get("attrs") or {}
    content = node.get("content") or []
    match node_type:
        case "paragraph":
            return _render_inline(content)
        case "heading":
            level = int(attrs.get("level") or 1)
            return f"{'#' * level} {_render_inline(content)}"
        case "bulletList":
            return _render_list(content, ordered=False)
        case "orderedList":
            return _render_list(content, ordered=True, start=int(attrs.get("order") or 1))
        case "taskList":
            return "\n".join(_render_task_item(item) for item in content)
        case "decisionList":
            return "\n".join(
                f"{_BULLET}{_render_inline(item.get('content') or [])}" for item in content
            )
        case "codeBlock":
            language = attrs.get("language") or ""
            code = "".join(child.get("text") or "" for child in content)
            return f"```{language}\n{code}\n```"
        case "blockquote":
            return _prefix_lines(_render_blocks(content), "> ")
        case "panel":
            panel_type = attrs.get("panelType")
            body = _render_blocks(content)
            if panel_type:
                body = f"**{str(panel_type).capitalize()}:** {body}"
            return _prefix_lines(body, "> ")
        case "rule":
            return "---"
        case "table":
            return _render_table(content)
        case "expand" | "nestedExpand":
            title = attrs.get("title")
            body = _render_blocks(content)
            return f"**{title}**\n\n{body}" if title else body
        case "mediaSingle" | "mediaGroup":
            return "\n".join(part for part in (_render_media(child) for child in content) if part)
        case "media":
            return _render_media(node)
        case _:
            if content and all(_is_inline(child) for child in content):
                return _render_inline(content)
            if content:
                return _render_blocks(content)
            return _render_inline([node])


def _render_list(items: list[dict[str, Any]], *, ordered: bool, start: int = 1) -> str:
    lines: list[str] = []
    for index, item in enumerate(items):
        marker = f"{start + index}. " if ordered else _BULLET
        body = _render_blocks(item.get("content") or [], separator="\n")
        lines.append(_indent_continuation(marker + body, " " * len(marker)))
    return "\n".join(lines)


def _render_task_item(item: dict[str, Any]) -> str:
    done = (item.get("attrs") or {}).get("state") == "DONE"
    content = item.get("content") or []
    if content and all(_is_inline(child) for child in content):
        body = _render_inline(content)
    else:
        body = _render_blocks(content, separator="\n")
    marker = f"{_BULLET}[{'x' if done else ' '}] "
    return _indent_continuation(marker + body, " " * len(marker))


def _render_table(rows: list[dict[str, Any]]) -> str:
    rendered_rows = [
        [
            _render_blocks(cell.get("content") or [], separator=" ")
            .replace("\n", " ")
            .replace("|", "\\|")
            for cell in row.get("content") or []
        ]
        for row in rows
    ]
    if not rendered_rows:
        return ""
    width = max(len(row) for row in rendered_rows)
    padded = [row + [""] * (width - len(row)) for row in rendered_rows]
    lines = [f"| {' | '.join(padded[0])} |", f"|{'---|' * width}"]
    lines.extend(f"| {' | '.join(row)} |" for row in padded[1:])
    return "\n".join(lines)


def _render_media(node: dict[str, Any]) -> str:
    attrs = node.get("attrs") or {}
    label = attrs.get("alt") or attrs.get("filename") or attrs.get("id")
    if not label:
        return ""
    url = attrs.get("url")
    return f"[attachment: {label}]({url})" if url else f"[attachment: {label}]"


_INLINE_TYPES = frozenset(
    {"text", "hardBreak", "mention", "emoji", "inlineCard", "status", "date", "placeholder"}
)


def _is_inline(node: dict[str, Any]) -> bool:
    return node.get("type") in _INLINE_TYPES


def _render_inline(nodes: list[dict[str, Any]]) -> str:
    return "".join(_render_inline_node(node) for node in nodes)


def _render_inline_node(node: dict[str, Any]) -> str:
    attrs = node.get("attrs") or {}
    match node.get("type"):
        case "text":
            return _apply_marks(node.get("text") or "", node.get("marks") or [])
        case "hardBreak":
            return "\n"
        case "mention":
            text = attrs.get("text") or attrs.get("id") or ""
            return text if text.startswith("@") else f"@{text}"
        case "emoji":
            return attrs.get("text") or attrs.get("shortName") or ""
        case "inlineCard" | "blockCard" | "embedCard":
            url = attrs.get("url") or ""
            return f"<{url}>" if url else ""
        case "status":
            return f"[{attrs.get('text') or ''}]"
        case "date":
            return str(attrs.get("timestamp") or "")
        case _:
            if node.get("content"):
                return _render_inline(node["content"])
            return str(attrs.get("text") or node.get("text") or "")


def _apply_marks(text: str, marks: list[dict[str, Any]]) -> str:
    if not text:
        return text
    link: str | None = None
    for mark in marks:
        match mark.get("type"):
            case "code":
                text = f"`{text}`"
            case "strong":
                text = f"**{text}**"
            case "em":
                text = f"*{text}*"
            case "strike":
                text = f"~~{text}~~"
            case "link":
                link = (mark.get("attrs") or {}).get("href")
    if link:
        text = f"[{text}]({link})"
    return text


def _prefix_lines(text: str, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" if line else prefix.rstrip() for line in text.split("\n"))


def _indent_continuation(text: str, indent: str) -> str:
    first, *rest = text.split("\n")
    return "\n".join([first, *(f"{indent}{line}" if line else line for line in rest)])
