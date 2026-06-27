#!/usr/bin/env python
"""Audit real resident autonomy proof artifacts across the 1-10 vision layers.

This script does not create proof. It inspects concrete artifacts produced by
real proof runs and reports which layers are currently supported by evidence.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class EvidenceRequirement:
    label: str
    pattern: str
    contains: tuple[str, ...] = ()


@dataclass(frozen=True)
class LayerAudit:
    key: str
    title: str
    root: Path
    requirements: tuple[EvidenceRequirement, ...]


@dataclass
class RequirementResult:
    label: str
    pattern: str
    status: str
    matches: list[str] = field(default_factory=list)
    missing_contains: list[str] = field(default_factory=list)


@dataclass
class LayerResult:
    key: str
    title: str
    root: str
    status: str
    requirements: list[RequirementResult]


_LAYER_TITLES = {
    "wakefulness": "Wakefulness Runtime",
    "long_horizon": "Long-Horizon Work Management",
    "memory": "Better Memory",
    "capability": "Tool and Capability Discovery",
    "delegation": "Remote Execution / Forge Orchestration",
    "policy": "Evolvable Policy",
    "physical": "Physical World Integration",
    "review": "Self-Review and Quality Loops",
    "opportunity": "Imagination / Opportunity Generation",
    "operator": "Operator Relationship",
}


_REQUIREMENTS = {
    "wakefulness": (
        EvidenceRequirement("wake cycle records", "**/resident/wakeful/cycles/*.md"),
    ),
    "long_horizon": (
        EvidenceRequirement("portfolio summary", "**/resident/portfolio/portfolio.md"),
        EvidenceRequirement("portfolio objectives", "**/resident/portfolio/objectives/*.md"),
    ),
    "memory": (
        EvidenceRequirement("domain model", "**/resident/domain-expert/domain-model.md"),
        EvidenceRequirement(
            "memory consolidation",
            "**/resident/domain-expert/consolidations/*.md",
        ),
    ),
    "capability": (
        EvidenceRequirement(
            "capability discovery records",
            "**/resident/capability-discovery/*.md",
        ),
    ),
    "delegation": (
        EvidenceRequirement("delegation records", "**/resident/delegations/*.md"),
        EvidenceRequirement(
            "delegation result records",
            "**/resident/delegation-results/*.md",
            contains=("backend_name: workflow",),
        ),
        EvidenceRequirement("delegation review records", "**/resident/delegation-reviews/*.md"),
    ),
    "policy": (
        EvidenceRequirement("policy decisions", "**/resident/continuation/policy-decisions/*.md"),
        EvidenceRequirement(
            "policy or operator observations",
            "**/resident/domain-expert/domain-model.md",
            contains=("policy",),
        ),
    ),
    "physical": (
        EvidenceRequirement(
            "physical capability records",
            "**/resident/physical/capabilities/*.md",
        ),
        EvidenceRequirement("physical audit records", "**/resident/physical/audits/*.md"),
        EvidenceRequirement("physical result records", "**/resident/physical/results/*.md"),
        EvidenceRequirement(
            "physical operator gate",
            "**/resident/continuation/operator-needed/latest.md",
            contains=("physical",),
        ),
    ),
    "review": (
        EvidenceRequirement("review records", "**/resident/reviews/*.md"),
        EvidenceRequirement("review audit records", "**/resident/reviews/audits/*.md"),
    ),
    "opportunity": (
        EvidenceRequirement("opportunity records", "**/resident/opportunities/*.md"),
        EvidenceRequirement("opportunity reports", "**/resident/opportunity-reports/*.md"),
        EvidenceRequirement(
            "opportunity work objectives",
            "**/resident/portfolio/objectives/opportunity-work-*.md",
        ),
    ),
    "operator": (
        EvidenceRequirement(
            "pending operator marker",
            "**/resident/continuation/operator-needed/latest.md",
        ),
        EvidenceRequirement(
            "consumed operator answer",
            "**/resident/continuation/operator-answers/latest.md",
            contains=("status: consumed", "consumed_at:"),
        ),
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        metavar="LAYER=PATH",
        help=(
            "Evidence root for one layer. Repeat for each layer. "
            f"Known layers: {', '.join(_LAYER_TITLES)}"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of markdown.",
    )
    parser.add_argument(
        "--allow-weak",
        action="store_true",
        help="Exit zero even when some layers are weak or missing.",
    )
    return parser.parse_args()


def _layer_roots(raw: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for item in raw:
        if "=" not in item:
            raise SystemExit(f"--evidence must be LAYER=PATH, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if key not in _LAYER_TITLES:
            raise SystemExit(f"unknown evidence layer {key!r}")
        roots[key] = Path(value).expanduser().resolve()
    return roots


def _audit_layer(audit: LayerAudit) -> LayerResult:
    results = [_audit_requirement(audit.root, requirement) for requirement in audit.requirements]
    if not audit.root.exists():
        status = "missing"
    elif all(result.status == "proved" for result in results):
        status = "proved"
    elif any(result.matches for result in results):
        status = "weak"
    else:
        status = "missing"
    return LayerResult(
        key=audit.key,
        title=audit.title,
        root=str(audit.root),
        status=status,
        requirements=results,
    )


def _audit_requirement(root: Path, requirement: EvidenceRequirement) -> RequirementResult:
    matches = sorted(path for path in root.glob(requirement.pattern) if path.is_file())
    missing_contains: list[str] = []
    if matches and requirement.contains:
        matched_content = "\n".join(_safe_read(path) for path in matches)
        missing_contains = [
            needle for needle in requirement.contains if needle not in matched_content
        ]
    status = "proved" if matches and not missing_contains else "missing"
    return RequirementResult(
        label=requirement.label,
        pattern=requirement.pattern,
        status=status,
        matches=[str(path) for path in matches[:12]],
        missing_contains=missing_contains,
    )


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(errors="ignore")


def _as_dict(result: LayerResult) -> dict[str, object]:
    return {
        "key": result.key,
        "title": result.title,
        "root": result.root,
        "status": result.status,
        "requirements": [
            {
                "label": item.label,
                "pattern": item.pattern,
                "status": item.status,
                "matches": item.matches,
                "missing_contains": item.missing_contains,
            }
            for item in result.requirements
        ],
    }


def _render_markdown(results: list[LayerResult]) -> str:
    lines = ["# Resident Vision Proof Audit", ""]
    for result in results:
        lines.append(f"## {result.title}: {result.status}")
        lines.append(f"- layer: `{result.key}`")
        lines.append(f"- root: `{result.root}`")
        for requirement in result.requirements:
            lines.append(
                f"- {requirement.label}: {requirement.status} "
                f"(`{requirement.pattern}`)"
            )
            for match in requirement.matches[:3]:
                lines.append(f"  - `{match}`")
            if requirement.missing_contains:
                lines.append(f"  - missing content: {', '.join(requirement.missing_contains)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> None:
    args = _parse_args()
    roots = _layer_roots(args.evidence)
    audits = [
        LayerAudit(
            key=key,
            title=_LAYER_TITLES[key],
            root=roots.get(key, Path("__missing_evidence_root__")),
            requirements=_REQUIREMENTS[key],
        )
        for key in _LAYER_TITLES
    ]
    results = [_audit_layer(audit) for audit in audits]
    if args.json:
        print(json.dumps([_as_dict(result) for result in results], indent=2))
    else:
        print(_render_markdown(results))
    if not args.allow_weak and any(result.status != "proved" for result in results):
        weak = ", ".join(result.key for result in results if result.status != "proved")
        raise SystemExit(f"[audit] unproved layers: {weak}")


if __name__ == "__main__":
    main()
