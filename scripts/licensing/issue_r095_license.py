from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.licensing import issue_license_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Issue an offline ScieFig R0.95 machine-bound license.")
    parser.add_argument("--private-key", required=True, type=Path, dest="private_key_path")
    parser.add_argument("--machine-code", required=True, type=str)
    parser.add_argument("--output", required=True, type=Path, dest="output_path")
    parser.add_argument("--license-id", required=True, type=str)
    parser.add_argument("--issued-at", required=False, type=str)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_path = issue_license_file(
        private_key_path=args.private_key_path,
        machine_code=args.machine_code,
        output_path=args.output_path,
        license_id=args.license_id,
        issued_at=args.issued_at,
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
