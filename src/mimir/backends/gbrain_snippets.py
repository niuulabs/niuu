"""Readable text for a search hit, from the chunk gbrain matched.

gbrain's ``query`` answers with the stored text of the chunk that matched, and the
gbrain backend stores more than what a person wrote: a page carries YAML front matter
with its sealed attribution (``mp1.…``), each timeline row its sealed provenance
(``mt1.<mac>.{json}``), and a raw source is a fenced JSON record whose ``content`` may
itself be HTML or Markdown. :func:`readable` turns such a chunk into the plain text it
holds. It removes markup and the backend's own bookkeeping; it never shortens the text.
"""

from __future__ import annotations

import html
import re

_SEAL = re.compile(r"\bm[pt]1\.[A-Za-z0-9_-]+\.")
_FRONT_MATTER = re.compile(r"\A\s*---\n.*?\n---\s*(?:\n|\Z)", re.DOTALL)
_ATTRIBUTION_LINE = re.compile(r"^\s*niuu_attribution:.*$", re.MULTILINE)
_FENCE_MARK = re.compile(r"```[a-z]*")
_CONTENT_KEY = re.compile(r'"content"\s*:\s*"')
_JSON_STRING = re.compile(r'(?:[^"\\]|\\.)*')
_JSON_ESCAPE = re.compile(r'\\(?:u([0-9a-fA-F]{4})|(["\\/bfnrt]))')
_DANGLING_ESCAPE = re.compile(r"\\(?:u[0-9a-fA-F]{0,3})?\Z")
_JSON_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": " ", "f": " ", "n": "\n", "r": " ", "t": " "}
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_SCRIPT = re.compile(r"<(script|style)\b.*?(?:</\1\s*>|\Z)", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^<>]*>")
_TRAILING_TAG = re.compile(r"<[^<>]*\Z")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+", re.MULTILINE)
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")


def readable(chunk: str, *, source: bool = False) -> str:
    """The plain text of *chunk*. *source* is true for a raw-source record's chunk."""
    text = _source_content(chunk) if source else chunk
    text = _drop_seals(text)
    text = _FRONT_MATTER.sub("", text)
    text = _ATTRIBUTION_LINE.sub("", text)
    text = _strip_html(text)
    text = _strip_markdown(text)
    return " ".join(text.split())


def _source_content(chunk: str) -> str:
    """The ``content`` string of a stored source record, or the part of it in *chunk*.

    A chunk may hold the whole record, start inside ``content`` or end inside it; a
    chunk of the record that holds none of ``content`` has nothing to read.
    """
    text = _FENCE_MARK.sub("", chunk).strip()
    key = _CONTENT_KEY.search(text)
    if key:
        text = text[key.end() :]
    elif text.startswith("{"):
        return ""
    fragment = _JSON_STRING.match(text)
    return _decode_json_string(fragment.group(0) if fragment else "")


def _decode_json_string(fragment: str) -> str:
    """Decode the escapes of a JSON string body, dropping one cut off at the end."""
    fragment = _DANGLING_ESCAPE.sub("", fragment)

    def unescape(match: re.Match[str]) -> str:
        if match.group(1):
            return chr(int(match.group(1), 16))
        return _JSON_ESCAPES[match.group(2)]

    return _JSON_ESCAPE.sub(unescape, fragment)


def _drop_seals(text: str) -> str:
    """Remove each sealed provenance token and the JSON object it carries."""
    out: list[str] = []
    position = 0
    for match in _SEAL.finditer(text):
        if match.start() < position:
            continue
        out.append(text[position : match.start()])
        position = _skip_object(text, match.end())
    out.append(text[position:])
    return "".join(out)


def _skip_object(text: str, start: int) -> int:
    """The index just past the JSON object at *start*, or the end if it is cut off."""
    if start >= len(text) or text[start] != "{":
        return start
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(text)


def _strip_html(text: str) -> str:
    text = _COMMENT.sub(" ", text)
    text = _SCRIPT.sub(" ", text)
    # A chunk may begin inside a tag: everything up to its closing ">" is markup.
    close, open_ = text.find(">"), text.find("<")
    if close != -1 and (open_ == -1 or close < open_) and _looks_like_markup(text[:close]):
        text = text[close + 1 :]
    text = _TAG.sub(" ", text)
    text = _TRAILING_TAG.sub(" ", text)
    return html.unescape(text)


def _looks_like_markup(prefix: str) -> bool:
    return '="' in prefix or prefix.rstrip().endswith(("/", '"'))


def _strip_markdown(text: str) -> str:
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _WIKILINK.sub(lambda m: m.group(2) or m.group(1), text)
    text = _LINK.sub(lambda m: m.group(1), text)
    return _EMPHASIS.sub("", text)
