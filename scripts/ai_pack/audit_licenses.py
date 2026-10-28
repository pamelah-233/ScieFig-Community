"""Audit AI Pack manifest for non-allowed commercial_use_status entries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="config/ai_pack/manifest.json")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any entry has commercial_use_status != 'allowed'.",
    )
    args = parser.parse_args()

    data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    restricted = [entry for entry in data.get("entries", []) if entry.get("commercial_use_status") != "allowed"]
    if not restricted:
        print(f"OK: all {len(data.get('entries', []))} entries are 'allowed'.")
        return 0

    print(f"Found {len(restricted)} entries with commercial_use_status != 'allowed':")
    for entry in restricted:
        print(
            f"  - {entry['model_id']:30s} "
            f"status={entry['commercial_use_status']:12s} "
            f"license={entry.get('license', '?')[:80]}"
        )
    if args.strict:
        return 1
    print("(informational mode — exit 0; pass --strict to fail the build.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
