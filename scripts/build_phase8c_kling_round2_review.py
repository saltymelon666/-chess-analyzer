from __future__ import annotations

import json
from pathlib import Path
import sys

import chess

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_phase8c_kling_review_page import build_page


CANDIDATES = ROOT / "docs" / "research" / "phase8c-kling-calibrated-candidates.json"
ADJUDICATION = ROOT / "docs" / "research" / "phase8c-kling-human-review-adjudication.json"
OUTPUT = ROOT / "docs" / "research" / "phase8c-kling-round2-review-boards.html"


def rotate_180(placement: str) -> str:
    board = chess.Board(f"{placement} w - - 0 1")
    return board.transform(chess.flip_vertical).transform(chess.flip_horizontal).board_fen()


def source_orientation_placement(read: dict) -> str:
    placement = read["placement"]
    if read.get("orientation") == "black":
        return rotate_180(placement)
    return placement


def _valid_turns(placement: str) -> list[str]:
    turns = []
    for turn in ("w", "b"):
        try:
            board = chess.Board(f"{placement} {turn} - - 0 1")
        except ValueError:
            continue
        if board.is_valid():
            turns.append(turn)
    return turns


def round2_cases(payload: dict, reviewed_files: set[str]) -> list[dict]:
    cases = []
    for item in payload["positions"]:
        if item["file"] in reviewed_files:
            continue
        placement = item["placement"]
        if (
            placement.count("K") == 1
            and placement.count("k") == 1
            and item["meanConfidence"] >= 0.90
            and (item.get("selectionMargin") or 0) >= 0.02
            and item.get("validTurns")
        ):
            cases.append({
                **item,
                "bookRead": {
                    "placement": placement,
                    "minConfidence": item["minConfidence"],
                    "meanConfidence": item["meanConfidence"],
                },
            })
    return cases


def main() -> None:
    payload = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    adjudication = json.loads(ADJUDICATION.read_text(encoding="utf-8"))
    reviewed_files = {item["file"] for item in adjudication["positions"]}
    cases = round2_cases(payload, reviewed_files)
    OUTPUT.write_text(build_page(
        cases,
        id_prefix="KH2",
        title="Kling/Horwitz 残局棋盘审核（第二批）",
        schema_version="phase8c-kling-review-2.0",
        download_name="phase8c-kling-round2-human-review-results.json",
        intro_text=f"共 {len(cases)} 个经第一批版式校准筛选的候选。",
    ), encoding="utf-8")
    print(json.dumps({"reviewCases": len(cases), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
