#!/usr/bin/env python3
"""Verify the Valkyrie flock proof evidence and write the proof report.

Reads the observer's JSONL event capture plus each resident's state directory
and asserts the full chain:

  signal -> micro-dream -> tool + skill on disk (teacher)
         -> flock.learning.proposed (with tool code + canary sample)
         -> peer canary + install + adoption ACK (student)
         -> flock-mismatch rejection (negative control)
         -> replayed signal handled by the built tool (teacher judgment)

Exits non-zero with the failed assertions when the chain is incomplete.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, help="observer JSONL capture")
    parser.add_argument("--out-dir", required=True, help="proof output directory")
    parser.add_argument("--teacher-state", required=True, help="teacher .ravn state dir")
    parser.add_argument("--student-state", required=True, help="student .ravn state dir")
    parser.add_argument("--teacher-id", default="valkyrie-k8s-a")
    parser.add_argument("--student-id", default="valkyrie-k8s-b")
    parser.add_argument("--control-id", default="valkyrie-printer")
    parser.add_argument("--transport", choices=["nng", "nats"], required=True)
    parser.add_argument("--expect-student-restart", action="store_true")
    parser.add_argument("--student-restart-marker", default="")
    parser.add_argument(
        "--expect-guarded-approval",
        action="store_true",
        help="Assert the guarded hold -> review request -> approve -> install round-trip",
    )
    return parser.parse_args()


def _load_events(path: Path) -> list[dict]:
    """Load captured events, deduplicated by event id.

    The observer may record one event twice (main subject + flock-scoped
    subject); the scoped capture wins so ``observed_subject`` is preserved.
    """
    by_id: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        key = event.get("event_id") or str(len(by_id))
        existing = by_id.get(key)
        if existing is None or event.get("observed_subject"):
            by_id[key] = event
    return list(by_id.values())


def _of_type(events: list[dict], event_type: str) -> list[dict]:
    return [event for event in events if event.get("event_type") == event_type]


def main() -> int:
    args = _parse_args()
    events_path = Path(args.events)
    if not events_path.is_file():
        print(f"FAIL: no event capture at {events_path}")
        return 1
    events = _load_events(events_path)
    failures: list[str] = []
    checks: list[tuple[str, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if ok:
            checks.append((name, detail))
        else:
            failures.append(f"{name}{f' — {detail}' if detail else ''}")

    # 1. Teacher micro-dream ran.
    dreams_started = _of_type(events, "valkyrie.dream.started")
    dreams_completed = _of_type(events, "valkyrie.dream.completed")
    check(
        "teacher dreamed",
        bool(dreams_started) and bool(dreams_completed),
        f"started={len(dreams_started)} completed={len(dreams_completed)}",
    )

    # 2. Teacher built and activated a skill + tool.
    activations = [
        event
        for event in _of_type(events, "valkyrie.evolution.activated")
        if event.get("payload", {}).get("valkyrie_id") == args.teacher_id
    ]
    check("teacher activated self-built skill", bool(activations))
    skill_name = activations[0]["payload"].get("skill_name", "") if activations else ""

    teacher_state = Path(args.teacher_state)
    teacher_skill = teacher_state / "skills" / f"{skill_name}.md"
    teacher_tool = teacher_state / "tools" / f"{skill_name}.py"
    check("teacher skill on disk", skill_name != "" and teacher_skill.is_file(), str(teacher_skill))
    check("teacher tool implementation on disk", teacher_tool.is_file(), str(teacher_tool))

    # 3. Flock proposal carried the implementation and a canary sample.
    proposals = _of_type(events, "flock.learning.proposed")
    proposal = next(
        (
            event
            for event in proposals
            if event.get("payload", {}).get("source_valkyrie_id") == args.teacher_id
        ),
        None,
    )
    check("teacher proposed learning to flock", proposal is not None)
    if proposal is not None:
        payload = proposal["payload"]
        check("proposal carries tool implementation", bool(payload.get("tool_code", "").strip()))
        check("proposal carries canary sample", bool(payload.get("canary_sample")))

    # 4. Student adopted after a passing canary.
    adoptions = _of_type(events, "learning.adoption.recorded")
    student_adoptions = [
        event
        for event in adoptions
        if event.get("payload", {}).get("resident_valkyrie_id") == args.student_id
        and event.get("payload", {}).get("action") == "adopted"
    ]
    check("student adopted the learning", bool(student_adoptions))
    check(
        "student adoption recorded exactly once",
        len(student_adoptions) == 1,
        f"student adoptions={len(student_adoptions)}",
    )
    if student_adoptions:
        payload = student_adoptions[0]["payload"]
        check("student canary actually passed", payload.get("canary_passed") is True)

    student_state = Path(args.student_state)
    student_skill = student_state / "skills" / f"{skill_name}.md"
    student_tool = student_state / "tools" / f"{skill_name}.py"
    check("student skill installed on disk", skill_name != "" and student_skill.is_file())
    check("student tool implementation installed on disk", student_tool.is_file())

    # 5. Negative control: the printer valkyrie rejected the k8s flock learning.
    control_rejections = [
        event
        for event in adoptions
        if event.get("payload", {}).get("resident_valkyrie_id") == args.control_id
        and event.get("payload", {}).get("action") == "rejected"
    ]
    check(
        "printer valkyrie rejected (flock mismatch)",
        any(
            "not a member" in event.get("payload", {}).get("rationale", "")
            for event in control_rejections
        ),
        f"rejections={len(control_rejections)}",
    )

    # 6. Replayed signal handled by the built tool (teacher judgment evidence).
    judgments = [
        event
        for event in _of_type(events, "valkyrie.judgment.proposed")
        if event.get("payload", {}).get("valkyrie_id") == args.teacher_id
        and event.get("payload", {}).get("recommended_action") == "inspect_with_adopted_learning"
    ]
    tool_executed = any(
        any(
            item.get("tool_executed") is True and item.get("tool_ok") is True
            for item in event.get("payload", {}).get("evidence", [])
            if isinstance(item, dict)
        )
        for event in judgments
    )
    check("replayed signal exercised the built tool", tool_executed, f"judgments={len(judgments)}")

    # 6b. Durable flock-learning ledger: the student's adoption survives on
    #     disk with provenance (F5/NIU-1034).
    ledger_path = student_state / "flock_learning.json"
    ledger_ok = False
    ledger_detail = f"missing {ledger_path}"
    if ledger_path.is_file():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        records = ledger.get("flock_learnings", [])
        adopted = [
            record
            for record in records
            if record.get("status") == "adopted"
            and any(
                decision.get("action") == "adopted" and decision.get("canary_passed")
                for decision in record.get("peer_decisions", [])
            )
        ]
        ledger_ok = bool(adopted)
        ledger_detail = f"records={len(records)} adopted={len(adopted)}"
    check("student adoption persisted in durable ledger", ledger_ok, ledger_detail)

    # 7. ODIN court resolved resident judgments into attention decisions
    #    inside the live daemons (NIU-1021 / F1).
    attention_decisions = [
        event
        for event in _of_type(events, "attention.decision.made")
        if event.get("payload", {}).get("environment_id", "").startswith("cluster")
    ]
    check(
        "ODIN court resolved judgments into attention decisions",
        bool(attention_decisions),
        f"decisions={len(attention_decisions)}",
    )
    court_decisions = _of_type(events, "odin.court.decided")
    check(
        "court decisions published with audit trail",
        any(
            event.get("payload", {}).get("audit_ref") or event.get("payload", {}).get("court_id")
            for event in court_decisions
        ),
        f"court events={len(court_decisions)}",
    )

    # 8. Feedback round-trip: injected operator feedback was consumed by the
    #    resident's recorder, which published the preference update (F3).
    preference_updates = _of_type(events, "feedback.preference.updated")
    check(
        "feedback recorder consumed operator feedback",
        any(
            event.get("payload", {}).get("delivery_state") == "snoozed"
            for event in preference_updates
        ),
        f"preference updates={len(preference_updates)}",
    )

    # 9. Wakefulness: the teacher transitioned through wakeful states and ran
    #    a scheduled consolidation dream (NIU-1040).
    state_changes = [
        event
        for event in _of_type(events, "valkyrie.state.changed")
        if event.get("payload", {}).get("valkyrie_id") == args.teacher_id
    ]
    observed_states = {event["payload"].get("new_state") for event in state_changes}
    check(
        "teacher wakefulness transitions observed",
        "watching" in observed_states and "wakeful" in observed_states,
        f"states={sorted(s for s in observed_states if s)}",
    )
    consolidations = [
        event
        for event in _of_type(events, "valkyrie.dream.completed")
        if event.get("payload", {}).get("dream_kind") == "consolidation"
    ]
    check(
        "scheduled consolidation dream completed",
        bool(consolidations),
        f"consolidation dreams={len(consolidations)}",
    )

    # 10. Guarded approval round-trip: the build was held behind a ReviewItem,
    #     the operator approved it, and the resident applied + confirmed.
    if args.expect_guarded_approval:
        held = [
            event
            for event in _of_type(events, "valkyrie.evolution.held")
            if event.get("payload", {}).get("valkyrie_id") == args.teacher_id
        ]
        check("guarded teacher held the build", bool(held), f"held events={len(held)}")

        requested = [
            event
            for event in _of_type(events, "odin.review.requested")
            if event.get("payload", {}).get("kind") == "evolution_build"
            and event.get("payload", {}).get("valkyrie_id") == args.teacher_id
        ]
        check("teacher filed an evolution_build review item", bool(requested))
        if requested:
            evidence = requested[0].get("payload", {}).get("evidence", {})
            artifact = evidence.get("artifact", {}) if isinstance(evidence, dict) else {}
            check(
                "review item carries the full artifact",
                bool(str(artifact.get("content", "")).strip())
                and bool(str(artifact.get("tool_code", "")).strip()),
            )

        decided = [
            event
            for event in _of_type(events, "odin.review.decided")
            if event.get("payload", {}).get("status") == "approved"
        ]
        check("operator approved the review item", bool(decided))
        if decided:
            check(
                "approval names the operator",
                decided[0].get("payload", {}).get("decided_by") == "human:proof-operator",
            )

        resolved = [
            event
            for event in _of_type(events, "odin.review.resolved")
            if event.get("payload", {}).get("apply_outcome") == "applied"
            and event.get("payload", {}).get("kind") == "evolution_build"
        ]
        check(
            "resident confirmed the applied decision",
            bool(resolved),
            f"resolved events={len(resolved)}",
        )

        outbox_path = teacher_state / "review_outbox.json"
        outbox_ok = False
        outbox_detail = f"missing {outbox_path}"
        if outbox_path.is_file():
            outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
            applied_items = [
                item
                for item in outbox.get("items", [])
                if item.get("kind") == "evolution_build" and item.get("status") == "applied"
            ]
            outbox_ok = bool(applied_items)
            outbox_detail = f"items={len(outbox.get('items', []))} applied={len(applied_items)}"
        check("approval persisted in the teacher's review outbox", outbox_ok, outbox_detail)

    # 11. NATS only: scoped flock subject fan-out observed.
    if args.transport == "nats":
        scoped = [
            event
            for event in events
            if str(event.get("observed_subject", "")).startswith("flock.")
            and event.get("event_type") in {"flock.learning.proposed", "learning.adoption.recorded"}
        ]
        check(
            "flock-scoped NATS subject fan-out observed",
            bool(scoped),
            f"scoped events={len(scoped)}",
        )
        if args.expect_student_restart:
            marker = Path(args.student_restart_marker)
            check("student restarted during NATS proof", marker.is_file(), str(marker))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "transport": args.transport,
        "passed": not failures,
        "checks_passed": [name for name, _ in checks],
        "failures": failures,
        "skill_name": skill_name,
        "event_count": len(events),
        "artifacts": {
            "teacher_skill": str(teacher_skill),
            "teacher_tool": str(teacher_tool),
            "student_skill": str(student_skill),
            "student_tool": str(student_tool),
            "events": str(events_path),
        },
    }
    (out_dir / "proof-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        "# Valkyrie Flock Proof",
        "",
        f"Transport: **{args.transport}**",
        f"Result: **{'PASSED' if not failures else 'FAILED'}**",
        f"Events captured: {len(events)}",
        f"Self-built skill: `{skill_name}`",
        "",
        "## Checks",
        "",
    ]
    lines += [f"- [x] {name}" + (f" ({detail})" if detail else "") for name, detail in checks]
    lines += [f"- [ ] {failure}" for failure in failures]
    (out_dir / "proof-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n{'=' * 60}")
    for name, detail in checks:
        print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))
    for failure in failures:
        print(f"  FAIL  {failure}")
    print(f"{'=' * 60}")
    print(f"report: {out_dir / 'proof-report.md'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
