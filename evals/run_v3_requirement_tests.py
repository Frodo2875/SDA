"""Run deduplicated pytest nodes for each formal V3 requirement family."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAPPING = PROJECT_ROOT / "evals" / "v3_requirement_traceability.json"
FAMILIES = ("F", "O", "R", "WF", "S")


def _family(requirement_id: str) -> str:
    return "WF" if requirement_id.startswith("WF") else requirement_id[0]


def main() -> int:
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    exit_code = 0
    for family in FAMILIES:
        nodes = list(dict.fromkeys(
            node
            for requirement_id, mapped in mapping.items()
            if _family(requirement_id) == family
            for node in mapped
        ))
        print(f"\n=== {family} formal tests ({len(nodes)} unique nodes) ===", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *nodes],
            cwd=PROJECT_ROOT,
            check=False,
        )
        exit_code = max(exit_code, completed.returncode)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
