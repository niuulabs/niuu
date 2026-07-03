"""Unit tests for the shared Postgres sanitizer used by the outbound adapters.

NUL bytes / surrogates are always built at runtime via ``chr(...)`` — never pasted
as a literal into the source (a literal NUL corrupts the file, which is the very
bug here). NUL (U+0000) and lone UTF-16 surrogates (U+D800–U+DFFF) are the chars
Postgres text/JSONB rejects; both are replaced with U+FFFD (lossless-ish), and
valid content — including astral characters — is preserved.
"""

import json

from volundr.adapters.outbound._jsonb import (
    REPLACEMENT,
    dumps_jsonb,
    force_scrub_json,
    scrub_text,
)

NUL = chr(0)
SURROGATE = chr(0xD800)  # a lone UTF-16 surrogate — also untranslatable
R = REPLACEMENT  # U+FFFD


def test_no_nul_is_byte_for_byte_identical_to_json_dumps():
    value = {"a": 1, "b": ["x", "y"], "c": {"d": True, "e": None}}
    assert dumps_jsonb(value) == json.dumps(value)


def test_nul_replaced_in_string_value():
    out = dumps_jsonb({"k": "a" + NUL + "b"})
    assert "\\u0000" not in out
    assert json.loads(out)["k"] == "a" + R + "b"


def test_nul_replaced_when_nested_in_dict_and_list():
    value = {"outer": {"inner": ["ok", "bad" + NUL + "end"]}}
    out = dumps_jsonb(value)
    assert "\\u0000" not in out
    assert json.loads(out)["outer"]["inner"][1] == "bad" + R + "end"


def test_nul_replaced_in_dict_key():
    out = dumps_jsonb({"meta" + NUL + "key": "v"})
    assert "\\u0000" not in out
    assert json.loads(out) == {"meta" + R + "key": "v"}


def test_multiple_nuls_all_replaced():
    out = dumps_jsonb({"k": NUL + "a" + NUL + NUL + "b" + NUL})
    assert "\\u0000" not in out
    assert json.loads(out)["k"] == R + "a" + R + R + "b" + R


def test_lone_surrogate_replaced_in_value_and_key():
    out = dumps_jsonb({"s" + SURROGATE: "p" + SURROGATE + "q"})
    lowered = out.lower()
    assert "\\ud800" not in lowered and "\\u0000" not in lowered
    assert json.loads(out) == {"s" + R: "p" + R + "q"}


def test_valid_non_ascii_and_astral_unicode_preserved():
    value = {"k": "café ✓ 日本語 😀"}  # emoji is a single astral codepoint, not a surrogate pair
    out = dumps_jsonb(value)
    assert json.loads(out)["k"] == "café ✓ 日本語 😀"
    assert out == json.dumps(value)


def test_non_string_dict_keys_left_intact():
    # json.dumps coerces int keys to strings; the sanitizer must not interfere.
    out = dumps_jsonb({1: "a" + NUL, 2: "b"})
    assert "\\u0000" not in out
    assert json.loads(out) == {"1": "a" + R, "2": "b"}


def test_tuple_serializes_as_array_with_nul_replaced():
    out = dumps_jsonb(("a" + NUL + "b", "c"))
    assert "\\u0000" not in out
    assert json.loads(out) == ["a" + R + "b", "c"]


def test_scrub_text_replaces_nul_and_surrogate_and_passes_through_non_str():
    assert scrub_text("r" + NUL + "id") == "r" + R + "id"
    assert scrub_text("x" + SURROGATE) == "x" + R
    assert scrub_text("clean café 😀") == "clean café 😀"
    assert scrub_text(None) is None
    assert scrub_text(42) == 42


def test_force_scrub_json_replaces_escaped_nul_and_surrogate():
    # Defensive layer operates on an already-serialized JSON string.
    scrubbed = force_scrub_json('{"k":"a\\u0000b\\ud800c"}')
    assert "\\u0000" not in scrubbed and "\\ud800" not in scrubbed.lower()
    assert json.loads(scrubbed)["k"] == "a" + R + "b" + R + "c"
