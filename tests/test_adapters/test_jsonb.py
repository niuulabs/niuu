"""Unit tests for the shared JSONB NUL-sanitizer used by the outbound adapters.

NUL bytes are always built at runtime via ``chr(0)`` — never pasted as a literal
into the source (a literal NUL corrupts the file, which is the very bug here).
"""

import json

from volundr.adapters.outbound._jsonb import dumps_jsonb

NUL = chr(0)


def test_no_nul_is_byte_for_byte_identical_to_json_dumps():
    value = {"a": 1, "b": ["x", "y"], "c": {"d": True, "e": None}}
    assert dumps_jsonb(value) == json.dumps(value)


def test_nul_stripped_from_string_value():
    out = dumps_jsonb({"k": "a" + NUL + "b"})
    assert "\\u0000" not in out
    assert json.loads(out)["k"] == "ab"


def test_nul_stripped_when_nested_in_dict_and_list():
    value = {"outer": {"inner": ["ok", "bad" + NUL + "end"]}}
    out = dumps_jsonb(value)
    assert "\\u0000" not in out
    assert json.loads(out)["outer"]["inner"][1] == "badend"


def test_nul_stripped_from_dict_key():
    out = dumps_jsonb({"meta" + NUL + "key": "v"})
    assert "\\u0000" not in out
    assert json.loads(out) == {"metakey": "v"}


def test_multiple_nuls_all_removed():
    out = dumps_jsonb({"k": NUL + "a" + NUL + NUL + "b" + NUL})
    assert "\\u0000" not in out
    assert json.loads(out)["k"] == "ab"


def test_valid_non_ascii_unicode_preserved():
    value = {"k": "café ✓ 日本語"}
    out = dumps_jsonb(value)
    assert json.loads(out)["k"] == "café ✓ 日本語"
    assert out == json.dumps(value)


def test_non_string_dict_keys_left_intact():
    # json.dumps coerces int keys to strings; the sanitizer must not interfere.
    out = dumps_jsonb({1: "a" + NUL, 2: "b"})
    assert "\\u0000" not in out
    assert json.loads(out) == {"1": "a", "2": "b"}


def test_tuple_serializes_as_array_with_nul_stripped():
    out = dumps_jsonb(("a" + NUL + "b", "c"))
    assert "\\u0000" not in out
    assert json.loads(out) == ["ab", "c"]
