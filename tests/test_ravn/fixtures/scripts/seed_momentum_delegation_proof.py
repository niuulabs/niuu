from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.resident_inbox.serialization import parse_inbox_signal

FIXTURES = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path(os.environ.get("TMPDIR", "/tmp")) / "ravn-niu1079-proof"
ROOT_ENV = "RAVN_MOMENTUM_DELEGATION_PROOF_ROOT"


async def seed(root: Path) -> None:
    state = LocalResidentState(root / "state")
    current = (FIXTURES / "momentum_attention_current_state.md").read_text(
        encoding="utf-8"
    )
    current_ref = await state.write_artifact(
        "resident/continuation/momentum/state/current.md",
        current,
    )
    mimir = MarkdownMimirAdapter(root=root / "mimir")
    refs = []
    for name, ref in [
        (
            "momentum_attention_signal_relevant.md",
            "resident/inbox/signals/20260628T100500Z-current-state-attention.md",
        ),
        (
            "momentum_attention_signal_distractor.md",
            "resident/inbox/signals/20260628T100400Z-distractor.md",
        ),
    ]:
        content = (FIXTURES / name).read_text(encoding="utf-8")
        signal = parse_inbox_signal(content)
        await mimir.upsert_page(ref, content)
        refs.append((ref, signal.id, signal.summary))

    print(f"proof_root: {root}")
    print(f"current_state_ref: {current_ref}")
    for ref, signal_id, summary in refs:
        print(f"candidate_ref: {ref}")
        print(f"candidate_id: {signal_id}")
        print(f"candidate_summary: {summary}")


def _root_arg() -> Path:
    parser = argparse.ArgumentParser(
        description="Seed committed fixtures for the NIU-1079 Momentum delegation proof."
    )
    parser.add_argument(
        "--root",
        default=os.environ.get(ROOT_ENV, str(DEFAULT_ROOT)),
        help=f"Temporary proof root. Defaults to ${ROOT_ENV} or {DEFAULT_ROOT}.",
    )
    args = parser.parse_args()
    return Path(args.root).expanduser().resolve()


if __name__ == "__main__":
    asyncio.run(seed(_root_arg()))
