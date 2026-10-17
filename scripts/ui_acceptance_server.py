"""Run the unchanged backend against synthetic, isolated UI acceptance data.

Usage: python scripts/ui_acceptance_server.py --env-file PATH --data-root /tmp/...
Credentials are loaded into this process only and never included in evidence.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args()
    root = args.data_root.resolve()
    if root == PROJECT_ROOT or PROJECT_ROOT in root.parents or root == Path(root.anchor):
        parser.error("Acceptance data must be outside the project and filesystem root")
    if not args.env_file.is_file():
        parser.error("Environment file not found")
    load_dotenv(args.env_file, override=True)
    os.environ["NO_PROXY"] = ",".join(filter(None, [os.getenv("NO_PROXY", ""), "localhost", "127.0.0.1", "::1"]))
    root.mkdir(parents=True, exist_ok=True)

    from backend import database
    from backend.tools import excel_utils

    database.DB_PATH = root / "acceptance.db"
    excel_utils.DATA_DIR = root
    database.initialize_database()

    from backend import agent
    from backend.llm_client import LLMClient

    class ObservedClient(LLMClient):
        """Measure real calls without replacing model responses or recording secrets."""

        async def create_chat_completion(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
            start = time.monotonic()
            result: dict[str, Any] = {"event": "real_model_call", "ok": False}
            try:
                response = await super().create_chat_completion(messages, tools)
                result["ok"] = True
                return response
            finally:
                result["seconds"] = round(time.monotonic() - start, 3)
                with (root / "model_metrics.jsonl").open("a") as evidence:
                    evidence.write(json.dumps(result) + "\n")

    agent.LLMClient = ObservedClient
    from backend.main import app
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
