from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.resident_inbox.serialization import parse_inbox_signal

FIXTURES = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path(os.environ.get("TMPDIR", "/tmp")) / "ravn-niu1080-proof"
ROOT_ENV = "RAVN_MOMENTUM_HANDOFF_PROOF_ROOT"
CURRENT_STATE_REF = "resident/continuation/momentum/state/current.md"
SIGNAL_REF = "resident/inbox/signals/20260628T100500Z-current-state-attention.md"
DISTRACTOR_REF = "resident/inbox/signals/20260628T100400Z-distractor.md"


async def seed(root: Path) -> None:
    state = LocalResidentState(root / "state")
    current_state = (FIXTURES / "momentum_attention_current_state.md").read_text(
        encoding="utf-8"
    )
    current_ref = await state.write_artifact(CURRENT_STATE_REF, current_state)

    mimir = MarkdownMimirAdapter(root=root / "mimir")
    signal_rows = []
    for name, ref in [
        ("momentum_attention_signal_relevant.md", SIGNAL_REF),
        ("momentum_attention_signal_distractor.md", DISTRACTOR_REF),
    ]:
        content = (FIXTURES / name).read_text(encoding="utf-8")
        signal = parse_inbox_signal(content)
        await mimir.upsert_page(ref, content)
        signal_rows.append((ref, signal.id, signal.summary))

    config_path = _write_config(root)

    print(f"proof_root: {root}")
    print(f"config: {config_path}")
    print(f"current_state_ref: {current_ref}")
    for ref, signal_id, summary in signal_rows:
        print(f"candidate_ref: {ref}")
        print(f"candidate_id: {signal_id}")
        print(f"candidate_summary: {summary}")


def _write_config(root: Path) -> Path:
    config = root / "ravn.yaml"
    config.write_text(
        "resident_state:\n"
        "  adapter: ravn.adapters.resident_state.mimir.LocalResidentState\n"
        "  kwargs:\n"
        f"    root: {root / 'state'}\n"
        "  fallback_adapter: ravn.adapters.resident_state.mimir.LocalResidentState\n"
        "  fallback_kwargs:\n"
        f"    root: {root / 'fallback-state'}\n"
        "mimir:\n"
        "  enabled: true\n"
        f"  path: {root / 'mimir'}\n"
        "permission:\n"
        "  mode: workspace_write\n"
        f"  workspace_root: {root}\n"
        "llm:\n"
        "  model: claude-opus-4-8[1m]\n"
        "  max_tokens: 8192\n"
        "  timeout: 180\n"
        "  provider:\n"
        "    adapter: ravn.adapters.llm.command.CommandLLMAdapter\n"
        "    kwargs:\n"
        "      command: /opt/homebrew/bin/claude\n"
        "      args:\n"
        "        - -p\n"
        "        - --output-format\n"
        "        - text\n"
        "momentum_executor:\n"
        "  adapter: ravn.adapters.executors.cli.CliTransportExecutor\n"
        "  kwargs:\n"
        "    transport_adapter: skuld.transports.codex.CodexSubprocessTransport\n"
        "    transport_kwargs:\n"
        "      model: ''\n"
        "      skip_git_repo_check: true\n",
        encoding="utf-8",
    )
    return config


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed committed fixtures for the NIU-1080 Momentum handoff proof."
    )
    parser.add_argument(
        "--root",
        default=os.environ.get(ROOT_ENV, str(DEFAULT_ROOT)),
        help=f"Temporary proof root. Defaults to ${ROOT_ENV} or {DEFAULT_ROOT}.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    parsed = _args()
    asyncio.run(seed(Path(parsed.root).expanduser().resolve()))
