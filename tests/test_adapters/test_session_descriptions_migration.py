"""Migration 000092 adds the session description columns and adopts the applied 000087."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "000092_session_descriptions"
# Thor production applied this SQL as 000087 before upstream took that number.
APPLIED_AS = "000087_session_descriptions.up.sql"
APPLIED_SHA256 = "3d47e0afee0c560b32bc66ae6d9857cd277806564ae748d55917d77b5ba99b88"
COLUMNS = (
    "short_description TEXT",
    "description TEXT",
    "short_description_updated_at TIMESTAMPTZ",
    "description_updated_at TIMESTAMPTZ",
)


def test_lineage_alias_adopts_the_applied_filename():
    aliases = json.loads((ROOT / "migrations" / "lineage-aliases.json").read_text())
    up = (ROOT / "migrations" / f"{NAME}.up.sql").read_bytes()
    assert aliases[f"{NAME}.up.sql"] == [
        {"filename": APPLIED_AS, "sha256": APPLIED_SHA256},
        {"filename": "000089_session_descriptions.up.sql", "sha256": APPLIED_SHA256},
    ]
    assert hashlib.sha256(up).hexdigest() == APPLIED_SHA256


def test_up_adds_four_nullable_idempotent_columns_and_down_drops_them():
    up = (ROOT / "migrations" / f"{NAME}.up.sql").read_text()
    down = (ROOT / "migrations" / f"{NAME}.down.sql").read_text()
    for column in COLUMNS:
        assert f"ALTER TABLE sessions ADD COLUMN IF NOT EXISTS {column};" in up
        assert f"DROP COLUMN IF EXISTS {column.split()[0]};" in down
    assert "NOT NULL" not in up and "DEFAULT" not in up
