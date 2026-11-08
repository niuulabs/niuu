from niuu.adapters.cli.runtime import filter_cli_event


def test_filter_cli_event_drops_keepalive_and_empty_deltas() -> None:
    assert filter_cli_event({"type": "keep_alive"}) is None
    assert filter_cli_event({"type": "content_block_delta", "delta": {}}) is None
    assert filter_cli_event({"type": "content_block_delta", "delta": {"text": "hi"}}) == {
        "type": "content_block_delta",
        "delta": {"text": "hi"},
    }
