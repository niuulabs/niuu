"""Extract native gbrain dream reports from mixed progress and JSON log output."""

import json
import re


def dream_results(output: str) -> list[dict]:
    decoder = json.JSONDecoder()
    reports = []
    for match in re.finditer(r"(?m)^\{", output):
        try:
            report, _ = decoder.raw_decode(output[match.start() :])
        except ValueError:
            continue
        if not isinstance(report, dict) or not isinstance(report.get("phases"), list):
            continue
        if not report.get("timestamp") or not report.get("status"):
            continue
        reports.append(
            {
                key: report[key]
                for key in ("timestamp", "status", "duration_ms", "phases", "totals")
                if key in report
            }
        )
    return reports[-10:]
