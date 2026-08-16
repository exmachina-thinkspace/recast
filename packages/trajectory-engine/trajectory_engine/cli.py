"""Command-line interface for the isolated trajectory engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import InputError, analyze_trajectory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic 1–3 year building trajectory scenarios."
    )
    parser.add_argument("--input", required=True, type=Path, help="Input JSON file")
    parser.add_argument("--output", type=Path, help="Optional output JSON file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with args.input.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        result = analyze_trajectory(payload)
    except (OSError, json.JSONDecodeError, InputError) as exc:
        print(f"trajectory-engine: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(result, indent=2, sort_keys=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
