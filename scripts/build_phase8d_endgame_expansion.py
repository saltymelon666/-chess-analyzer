from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from urllib.parse import quote
from urllib.request import urlopen

import chess

from scripts.export_phase8c_endgame_knowledge import build_runtime_dataset


ROOT = Path(__file__).resolve().parents[1]
BOOK_DATASET = ROOT / "docs" / "research" / "phase7i-book-ground-truth-dataset.json"
KLING_DATASET = ROOT / "docs" / "research" / "phase8c-kling-endgame-dataset.json"
RESEARCH_OUTPUT = ROOT / "docs" / "research" / "phase8d-endgame-expansion-audit.json"
RUNTIME_OUTPUT = ROOT / "app" / "data" / "endgame-knowledge.json"
TABLEBASE_URL = "https://tablebase.lichess.ovh/standard?fen="


# These entries are retained in the research audit but excluded from product lookup.
# The decision is about source/FEN scope or instructional value, never copyright.
EXCLUSIONS = {
    "CC-AUTO-014": "source_scope_conflict",
    "CF-AUTO-29": "book_tablebase_conflict",
    "CLASSIC-858f7899cff4394b": "non_instructional_comment",
    "CLASSIC-5b9bea2aab27a270": "non_instructional_comment",
    "CLASSIC-9d3aadd17e5678af": "non_instructional_comment",
}

SOURCE_YEARS = {
    "The Blue Book of Chess": 1897,
    "Chess and Checkers: The Way to Mastership": 1918,
    "Chess Fundamentals": 1921,
    "My Best Games of Chess 1908–1923 (1927)": 1927,
    "Chess Strategy (1921)": 1921,
    "The International Chess Congress, St. Petersburg, 1909 (1910)": 1910,
    "Morphy's Games of Chess (1860)": 1860,
    "The Modern Chess Instructor (1889)": 1889,
}


def default_probe(fen: str) -> dict:
    with urlopen(TABLEBASE_URL + quote(fen), timeout=20) as response:
        return json.load(response)


def white_outcome(category: str, turn: chess.Color) -> str:
    if category == "draw":
        return "draw"
    side_to_move_wins = category in {"win", "cursed-win"}
    winner = turn if side_to_move_wins else not turn
    return "white_win" if winner == chess.WHITE else "black_win"


def build_expansion(book_payload: dict, probe: Callable[[str], dict]) -> dict:
    cases = []
    for source_case in book_payload["cases"]:
        board = chess.Board(source_case["fen"])
        if len(board.piece_map()) > 7:
            continue
        root = probe(board.fen())
        moves = root.get("moves", [])
        if not moves:
            status = "tablebase_route_missing"
            key_move = None
        else:
            move = chess.Move.from_uci(moves[0]["uci"])
            if move not in board.legal_moves:
                raise ValueError(f"Syzygy returned illegal move for {source_case['position_id']}")
            key_move = {"uci": move.uci(), "san": board.san(move)}
            status = EXCLUSIONS.get(source_case["position_id"], "exact_verified")
        book_move = None
        if source_case.get("annotated_move_uci"):
            move = chess.Move.from_uci(source_case["annotated_move_uci"])
            if move in board.legal_moves:
                book_move = {"uci": move.uci(), "san": board.san(move)}
        case = {
            "id": source_case["position_id"],
            "placement": board.board_fen(),
            "sideToMove": "w" if board.turn == chess.WHITE else "b",
            "pieceCount": len(board.piece_map()),
            "outcome": white_outcome(root["category"], board.turn),
            "sourcePhrase": source_case["reference_explanation"],
            "source": {
                "title": source_case["source_title"],
                "author": source_case["author"],
                "year": SOURCE_YEARS.get(source_case["source_title"]),
                "locator": source_case["locator"],
                "url": source_case["source_url"],
                "rightsBoundary": source_case["rights_boundary"],
            },
            "tablebase": {
                "category": root["category"],
                "dtz": root.get("dtz"),
                "dtm": root.get("dtm"),
                "computedWhiteOutcome": white_outcome(root["category"], board.turn),
            },
            "keyMove": key_move,
            "bookMove": book_move,
            "admissionStatus": status,
            "verificationMethod": "live_lichess_syzygy",
        }
        cases.append(case)
    return {
        "schemaVersion": "phase8d-endgame-expansion-audit-1.0",
        "authorityBoundary": (
            "棋书文字只绑定完全相同的源局面；胜和负与关键着由Syzygy决定。"
            "冲突、范围错配和无教学内容的记录不进入产品查询。"
        ),
        "summary": {
            "candidateCount": len(cases),
            "exactVerifiedCount": sum(c["admissionStatus"] == "exact_verified" for c in cases),
            "excludedCount": sum(c["admissionStatus"] != "exact_verified" for c in cases),
        },
        "cases": cases,
    }


def merge_runtime(kling_payload: dict, expansion: dict) -> dict:
    base = build_runtime_dataset(kling_payload)
    positions = list(base["positions"])
    seen = {(p["placement"], p["sideToMove"]) for p in positions}
    for case in expansion["cases"]:
        if case["admissionStatus"] != "exact_verified" or not case["keyMove"]:
            continue
        key = (case["placement"], case["sideToMove"])
        if key in seen:
            continue
        positions.append({
            key: value for key, value in case.items()
            if key not in {"pieceCount", "admissionStatus", "verificationMethod"}
        })
        seen.add(key)
    return {
        "schemaVersion": "endgame-knowledge-1.1",
        "sourceTitle": "Multiple verified chess books",
        "sourceYear": None,
        "positions": positions,
    }


def main() -> None:
    book_payload = json.loads(BOOK_DATASET.read_text(encoding="utf-8"))
    kling_payload = json.loads(KLING_DATASET.read_text(encoding="utf-8"))
    expansion = build_expansion(book_payload, default_probe)
    runtime = merge_runtime(kling_payload, expansion)
    RESEARCH_OUTPUT.write_text(
        json.dumps(expansion, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    RUNTIME_OUTPUT.write_text(
        json.dumps(runtime, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(json.dumps({
        "audit": expansion["summary"],
        "runtimePositions": len(runtime["positions"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
