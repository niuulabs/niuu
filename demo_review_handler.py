#!/usr/bin/env python3
"""
Demo script showing how to handle review.completed events
"""

import json
from datetime import datetime


def process_review_event():
    """Process the review.completed event from reviewer"""

    # Event data from context
    event_data = {
        "verdict": "needs_changes",
        "findings_count": 4,
        "critical_count": 2,
        "summary": (
            "Critical security vulnerabilities including SQL injection "
            "and command injection found in code changes"
        ),
        "comments": [
            (
                "/tmp/hello.py:6: SQL injection vulnerability - user input is "
                + "directly interpolated into SQL query without sanitization"
            ),
            (
                "/tmp/hello.py:10: Command injection vulnerability - "
                + "shell=True allows arbitrary command execution"
            ),
            "/tmp/hello.py:12: Hardcoded API key - security risk for credential exposure",
            "/tmp/hello.py:17: Bare except clause - catches all exceptions without proper handling",
        ],
    }

    print("=" * 60)
    print("REVIEW.COMPLETED EVENT PROCESSING DEMO")
    print("=" * 60)

    print(f"Event received at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Verdict: {event_data['verdict']}")
    print(
        f"Total findings: {event_data['findings_count']} (Critical: {event_data['critical_count']})"
    )
    print(f"Summary: {event_data['summary']}")

    print("\nDetailed findings:")
    for i, comment in enumerate(event_data["comments"], 1):
        print(f"  {i}. {comment}")

    print("\nRecommended actions:")
    if event_data["critical_count"] > 0:
        print("  • Address all critical security vulnerabilities immediately")

    print("  • Create remediation plan for all issues")
    print("  • Schedule follow-up review after fixes")
    print("  • Implement proper input validation")
    print("  • Remove hardcoded credentials")
    print("  • Fix exception handling patterns")

    return {
        "status": "handled",
        "timestamp": datetime.now().isoformat(),
        "verdict": event_data["verdict"],
        "actions_taken": [
            "Acknowledged review findings",
            "Identified critical issues",
            "Prepared remediation plan",
        ],
    }


if __name__ == "__main__":
    result = process_review_event()
    print(f"\nResult: {json.dumps(result, indent=2)}")
