import json

from mimir.dream_results import dream_results


def test_reads_complete_native_reports_among_progress_and_partial_output():
    report = {
        "timestamp": "2026-09-10T01:00:00Z",
        "status": "clean",
        "duration_ms": 25,
        "phases": [{"phase": "lint", "status": "skipped", "details": {"reason": "no_brain_dir"}}],
    }
    output = (
        '[progress] start\n{"started_at": "now"}\n'
        + json.dumps(report, indent=2)
        + '\n{"truncated":'
    )
    assert dream_results(output) == [report]
    assert dream_results("") == []
