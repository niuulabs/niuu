#!/usr/bin/env python
"""Run a bounded resident capability discovery proof."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path
from typing import Any

from niuu.utils import import_class, resolve_secret_kwargs
from ravn.adapters.capabilities.resident_discovery import builtin_tool_capabilities
from ravn.cli.commands import _build_mimir, _configure_logging
from ravn.config import Settings
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.adapters.resident_work.mimir import MimirResidentWorkAdapter
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    ResidentCapabilityDiscoveryConfig,
    ResidentCapabilityDiscoveryRuntime,
    detect_capability_gaps,
    render_capability_discovery_result,
)

AUTONOMY_MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts.\n"
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="", help="Path to ravn config YAML")
    parser.add_argument("--workspace", default="", help="Workspace root for proof artifacts")
    parser.add_argument("--mandate", default=AUTONOMY_MANDATE)
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Run against the existing configured portfolio instead of seeding proof data.",
    )
    parser.add_argument(
        "--seed-required-capability",
        default="generic evidence review",
        help="Capability name used only when seeding a proof portfolio.",
    )
    parser.add_argument(
        "--require-research-evidence",
        action="store_true",
        help="Fail unless the persisted result contains real research evidence.",
    )
    parser.add_argument(
        "--require-configuration-evidence",
        action="store_true",
        help="Fail unless the result contains dynamic adapter/workflow config evidence.",
    )
    parser.add_argument(
        "--require-duplicate-check",
        action="store_true",
        help="Fail unless duplicate/existing capability checks are persisted.",
    )
    parser.add_argument(
        "--require-existing-capability-check",
        action="store_true",
        help="Fail unless an existing catalog capability satisfies the selected gap.",
    )
    parser.add_argument(
        "--expect-adapter-suppressed",
        action="store_true",
        help="Fail if adapter-building follow-ups are created for an existing capability.",
    )
    parser.add_argument(
        "--run-configured-safe-experiment",
        action="store_true",
        help=(
            "Load the first configured adapter option and run a no-side-effect "
            "experiment against researched evidence."
        ),
    )
    return parser.parse_args()


def _objective(required_capability: str) -> ResidentObjective:
    return ResidentObjective(
        id="generic-capability-gap",
        title="Resolve generic missing capability",
        purpose="Find a safe path for a capability the resident does not yet have.",
        serves_mandate_because="The resident needs to expand capability without being told how.",
        expected_outcome="A discovery artifact and safe follow-up objectives exist.",
        proof_criteria=("Discovery artifact is persisted and linked.",),
        kind=ResidentObjectiveKind.TOOL_BUILDING.value,
        status=ResidentObjectiveStatus.CANDIDATE.value,
        required_capabilities=(required_capability,),
        risk_boundaries=("physical_operation",),
        source_evidence=(f"missing capability: {required_capability}",),
        reasoning="No safe execution path exists until the resident discovers options.",
    )


async def _seed_portfolio(backend: Any, mandate: str, required_capability: str) -> None:
    objective = _objective(required_capability)
    await backend.write_objective(objective)
    await backend.write_portfolio(
        ResidentPortfolio(
            mandate=mandate,
            objectives=(objective,),
            decision_history=("seeded resident capability discovery proof portfolio",),
        )
    )


def _build_discovery(settings: Settings) -> Any:
    cfg = settings.resident_capability_discovery
    cls = import_class(cfg.adapter)
    kwargs = resolve_secret_kwargs(dict(cfg.kwargs), dict(cfg.secret_kwargs_env))
    if cfg.include_builtin_catalog:
        existing = list(kwargs.get("catalog_capabilities") or [])
        existing.extend(_builtin_catalog_capabilities())
        kwargs["catalog_capabilities"] = existing
    return cls(**kwargs)


def _builtin_catalog_capabilities() -> list[dict[str, Any]]:
    return builtin_tool_capabilities()


async def _run_configured_safe_experiment(
    backend: Any,
    result: Any,
) -> str:
    option = next(
        (item for item in result.candidate_options if item.configuration.get("adapter")),
        None,
    )
    if option is None:
        raise SystemExit("[proof] expected configured adapter option")
    config = dict(option.configuration)
    cls = import_class(str(config["adapter"]))
    tool = cls(**dict(config.get("kwargs") or {}))
    schema = getattr(tool, "input_schema", {})
    configured_input = config.get("safe_experiment_input")
    if isinstance(configured_input, dict) and configured_input:
        return await _execute_safe_experiment(
            backend,
            result=result,
            option=option,
            adapter=str(config["adapter"]),
            tool=tool,
            attempts=[f"configured input: {configured_input}"],
            payloads=[dict(configured_input)],
        )

    urls = _researched_urls(result.research_evidence)
    if not urls:
        raise SystemExit("[proof] expected researched URL for safe adapter experiment")
    if "url" not in set(schema.get("required") or []) and "url" not in (
        schema.get("properties") or {}
    ):
        raise SystemExit("[proof] configured adapter does not accept URL input")
    return await _execute_safe_experiment(
        backend,
        result=result,
        option=option,
        adapter=str(config["adapter"]),
        tool=tool,
        attempts=[],
        payloads=[{"url": url} for url in urls],
    )


async def _execute_safe_experiment(
    backend: Any,
    *,
    result: Any,
    option: Any,
    adapter: str,
    tool: Any,
    attempts: list[str],
    payloads: list[dict[str, Any]],
) -> str:
    attempts = list(attempts)
    for payload in payloads:
        label = str(payload.get("url") or payload)
        output = await tool.execute(payload)
        is_error = bool(getattr(output, "is_error", False))
        output_content = str(getattr(output, "content", ""))
        empty = not output_content.strip()
        attempts.append(f"{label}: {'error' if is_error else 'empty' if empty else 'ok'}")
        if is_error or empty:
            continue
        content = (
            f"# Capability Discovery Safe Experiment\n\n"
            f"- gap_id: {result.gap.id}\n"
            f"- option_id: {option.id}\n"
            f"- adapter: {adapter}\n"
            f"- input: {payload}\n"
            f"- is_error: false\n\n"
            f"## Attempts\n\n{_render_attempts(attempts)}\n\n"
            f"## Output Preview\n\n{output_content[:2000]}\n"
        )
        return await backend.write_capability_discovery(
            f"safe-experiment-{result.gap.id}",
            content,
        )
    content = (
        f"# Capability Discovery Safe Experiment\n\n"
        f"- gap_id: {result.gap.id}\n"
        f"- option_id: {option.id}\n"
        f"- adapter: {adapter}\n"
        f"- is_error: true\n\n"
        f"## Attempts\n\n{_render_attempts(attempts)}\n"
    )
    await backend.write_capability_discovery(f"safe-experiment-{result.gap.id}", content)
    raise SystemExit("[proof] configured safe experiment returned errors for every URL")


def _researched_urls(evidence: tuple[str, ...]) -> list[str]:
    urls: list[str] = []
    for item in evidence:
        match = re.search(r"https?://[^\s|]+", str(item))
        if match and match.group(0) not in urls:
            urls.append(match.group(0))
    return urls


def _render_attempts(attempts: list[str]) -> str:
    return "\n".join(f"- {item}" for item in attempts) or "- none"


async def _main() -> None:
    args = _parse_args()
    if args.config:
        os.environ["RAVN_CONFIG"] = args.config
    if args.workspace:
        workspace = Path(args.workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        os.chdir(workspace)

    mandate = str(args.mandate).strip() or AUTONOMY_MANDATE
    settings = Settings()
    _configure_logging(settings)
    mimir = _build_mimir(settings)
    if mimir is not None:
        backend: Any = MimirResidentWorkAdapter(mimir)
        memory_label = "mimir"
    else:
        local_root = Path.cwd() / ".ravn"
        backend = LocalResidentWorkItemBackend(local_root)
        memory_label = str(local_root)

    seeded = not args.no_seed
    if seeded:
        await _seed_portfolio(backend, mandate, str(args.seed_required_capability))

    before_gaps = detect_capability_gaps(tuple(await backend.list_objectives(mandate)))
    discovery = _build_discovery(settings)
    discovery_cfg = settings.resident_capability_discovery
    report = await ResidentCapabilityDiscoveryRuntime(
        backend=backend,
        discovery=discovery,
        config=ResidentCapabilityDiscoveryConfig(
            max_options=discovery_cfg.max_options,
            max_follow_up_objectives=discovery_cfg.max_follow_up_objectives,
        ),
    ).run(mandate)
    safe_experiment_ref = ""
    if args.run_configured_safe_experiment and report.discovery_result is not None:
        safe_experiment_ref = await _run_configured_safe_experiment(
            backend,
            report.discovery_result,
        )
    after_objectives = await backend.list_objectives(mandate)

    print("[proof] Resident capability discovery proof.")
    print(f"[proof] memory={memory_label}")
    print(f"[proof] discovery_adapter={discovery_cfg.adapter}")
    print(f"[proof] include_builtin_catalog={discovery_cfg.include_builtin_catalog}")
    print(f"[proof] seeded={seeded}")
    print(f"[proof] gaps_before={len(before_gaps)}")
    print(
        f"[proof] selected_gap={report.selected_gap.capability if report.selected_gap else 'none'}"
    )
    print(f"[proof] persisted_refs={len(report.persisted_refs)}")
    print(f"[proof] objectives_after={len(after_objectives)}")
    print(f"[proof] operator_questions={len(report.operator_questions)}")
    print(f"[proof] safe_experiment_ref={safe_experiment_ref or 'none'}")
    print(f"[proof] final_suggested_next_action={report.final_suggested_next_action}")
    if report.discovery_result is not None:
        print(f"[proof] options={len(report.discovery_result.candidate_options)}")
        print(f"[proof] existing_capabilities={len(report.discovery_result.existing_capabilities)}")
        print(f"[proof] duplicate_check_notes={len(report.discovery_result.duplicate_check_notes)}")
        print(f"[proof] research_evidence={len(report.discovery_result.research_evidence)}")
        print(
            f"[proof] configuration_evidence="
            f"{len(report.discovery_result.configuration_evidence)}"
        )
        for option in report.discovery_result.candidate_options:
            print(
                "[proof] option="
                f"{option.id} approval={option.approval_required} "
                f"experiment={option.safe_next_experiment!r}"
            )
        print()
        print(render_capability_discovery_result(report.discovery_result))

    strict = any(
        (
            args.require_research_evidence,
            args.require_configuration_evidence,
            args.require_duplicate_check,
            args.require_existing_capability_check,
            args.expect_adapter_suppressed,
            args.run_configured_safe_experiment,
        )
    )
    if not seeded and not strict:
        return
    if seeded and not before_gaps:
        raise SystemExit("[proof] expected seeded portfolio capability gap")
    if report.discovery_result is None:
        raise SystemExit("[proof] expected discovery result")
    if not report.discovery_result.candidate_options:
        raise SystemExit("[proof] expected candidate options")
    if not report.discovery_result.recommended_safe_next_experiment:
        raise SystemExit("[proof] expected recommended safe experiment")
    if not any(ref.startswith("resident/capability-discovery/") for ref in report.persisted_refs):
        raise SystemExit("[proof] expected persisted discovery artifact")
    if args.require_duplicate_check and not report.discovery_result.duplicate_check_notes:
        raise SystemExit("[proof] expected duplicate-check notes")
    if args.require_existing_capability_check and not report.discovery_result.existing_capabilities:
        raise SystemExit("[proof] expected existing capability evidence")
    if args.require_research_evidence and not report.discovery_result.research_evidence:
        raise SystemExit("[proof] expected research evidence")
    if args.require_configuration_evidence and not report.discovery_result.configuration_evidence:
        raise SystemExit("[proof] expected configuration evidence")
    if args.expect_adapter_suppressed and any(
        any("adapter" in capability.casefold() for capability in objective.required_capabilities)
        for objective in after_objectives
        if objective.id != "generic-capability-gap"
    ):
        raise SystemExit("[proof] expected adapter-building follow-ups to be suppressed")
    if args.run_configured_safe_experiment and not safe_experiment_ref:
        raise SystemExit("[proof] expected persisted safe experiment result")
    if seeded and not any(
        objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
        for objective in after_objectives
    ):
        raise SystemExit("[proof] expected operator-gated follow-up objective")
    if seeded and not any(
        objective.status == ResidentObjectiveStatus.CANDIDATE.value
        and objective.kind == ResidentObjectiveKind.VERIFICATION.value
        for objective in after_objectives
    ):
        raise SystemExit("[proof] expected safe evaluation follow-up objective")
    source = next(
        (objective for objective in after_objectives if objective.id == "generic-capability-gap"),
        None,
    )
    if seeded and (source is None or not source.artifact_links):
        raise SystemExit("[proof] expected source objective linked to discovery artifact")


if __name__ == "__main__":
    asyncio.run(_main())
