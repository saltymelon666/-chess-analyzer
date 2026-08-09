from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "research" / "phase8c-kling-endgame-dataset.json"
OUTPUT = ROOT / "app" / "data" / "endgame-knowledge.json"


def build_runtime_dataset(payload: dict) -> dict:
    positions = []
    for case in payload["cases"]:
        if case["admissionStatus"] != "exact_verified":
            continue
        key_move = case["verifiedRoute"][0]
        positions.append({
            "id": case["id"],
            "placement": case["placement"],
            "sideToMove": case["sideToMove"],
            "outcome": case["bookClaim"],
            "sourcePage": case["sourcePage"],
            "sourcePhrase": case["sourcePhrase"],
            "source": {
                "title": payload["sourceTitle"],
                "year": payload["sourceYear"],
                "locator": f"p. {case['sourcePage']}",
            },
            "tablebase": case["tablebase"],
            "keyMove": {"uci": key_move["uci"], "san": key_move["san"]},
        })
    return {
        "schemaVersion": "endgame-knowledge-1.1",
        "sourceTitle": payload["sourceTitle"],
        "sourceYear": payload["sourceYear"],
        "positions": positions,
    }


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    runtime = build_runtime_dataset(payload)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(runtime, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(json.dumps({"positions": len(runtime["positions"]), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
