"""
Wipe the contaminated feature_observations_cb table.

All existing rows were extracted with the buggy v5_extract_features_cb.py that
suffered from a feature/label name collision (ret_3). They cannot be salvaged
and must not be used for any future training. See
docs/v5_cb_v2/v5_leakage_postmortem.md for the full write-up.

This script is idempotent: running it twice is safe (the second run will just
report 0 rows before/after).
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("/tmp/my-project/data/ppmt.db")
TABLE = "feature_observations_cb"


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERR] DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Count before
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    before = cur.fetchone()[0]
    print(f"[before] {TABLE}: {before} rows")

    # Wipe
    cur.execute(f"DELETE FROM {TABLE}")
    conn.commit()

    # Reset autoincrement so the next insert starts at 1
    cur.execute(
        "DELETE FROM sqlite_sequence WHERE name = ?", (TABLE,)
    )
    conn.commit()

    # VACUUM to reclaim space (runs outside a transaction)
    cur.close()
    conn.isolation_level = None
    cur2 = conn.cursor()
    cur2.execute("VACUUM")
    cur2.close()
    conn.close()

    # Verify
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    after = cur.fetchone()[0]
    print(f"[after]  {TABLE}: {after} rows")
    conn.close()

    if after != 0:
        print("[ERR] Wipe failed — rows still present", file=sys.stderr)
        return 2

    print(f"[ok] wiped {before} contaminated rows from {TABLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
