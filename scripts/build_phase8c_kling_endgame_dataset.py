from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from urllib.parse import quote
from urllib.request import urlopen

import chess


ROOT = Path(__file__).resolve().parents[1]
ADJUDICATIONS = (
    ROOT / "docs" / "research" / "phase8c-kling-human-review-adjudication.json",
    ROOT / "docs" / "research" / "phase8c-kling-round2-human-review-adjudication.json",
)
OUTPUT = ROOT / "docs" / "research" / "phase8c-kling-endgame-dataset.json"
TABLEBASE_URL = "https://tablebase.lichess.ovh/standard?fen="


SOURCE_ASSERTIONS = {
    "KH-001": ("w", "draw", "White can only draw.", 17),
    "KH-002": (None, None, "原图未保留结果图注；正文只保留着法和对王说明。", 20),
    "KH-003": ("w", "white_win", "White to move and win.", 36),
    "KH-004": (None, "black_win", "Black wins.", 45),
    "KH-005": ("w", "white_win", "White to move and win.", 46),
    "KH-006": ("w", "white_win", "White to move and win.", 50),
    "KH-007": ("w", "white_win", "White has the move and wins.", 65),
    "KH-008": ("w", "white_win", "White to move and win.", 74),
    "KH-009": ("w", "draw", "White can only draw.", 110),
    "KH-010": ("w", "white_win", "White to move and win.", 136),
    "KH-011": ("w", "draw", "White having the move draws.", 161),
    "KH-012": ("w", "draw", "White has the move and can but draw.", 166),
    "KH-013": ("w", "draw", "White has the move and can but draw.", 187),
    "KH-014": (None, None, "See example No. 6.", 204),
    "KH-015": ("w", "white_win", "White to move and win.", 216),
    "KH-016": ("w", "white_win", "White to move and win.", 225),
    "KH2-001": ("w", "draw", "White with the move can only draw.", 26),
    "KH2-002": ("w", "draw", "White with the move can only draw.", 33),
    "KH2-003": ("w", "white_win", "White to move and win.", 72),
    "KH2-004": ("w", "white_win", "White to move and win.", 83),
    "KH2-005": ("w", "white_win", "White to move and win.", 106),
    "KH2-006": ("w", "white_win", "White to move and win.", 155),
    "KH2-007": ("w", "draw", "White can only draw.", 156),
    "KH2-008": ("w", "white_win", "White to move and win.", 168),
    "KH2-009": (None, "white_win", "White wins.", 172),
    "KH2-010": ("w", "white_win", "White to move and win.", 174),
    "KH2-011": ("w", "white_win", "White to move and win.", 182),
    "KH2-012": ("w", "white_win", "White to move and win.", 212),
}

# Audited against the Lichess Syzygy API on 2026-08-09.  Only the root WDL,
# distance fields and first ranked move are frozen here.  Every move is rebuilt
# as SAN and checked against python-chess before it can enter the dataset.
TABLEBASE_ROOT_AUDIT = {
    "KH-001": ("draw", 0, 0, "d2d3"),
    "KH-003": ("win", 1, None, "g3g4"),
    "KH-005": ("win", 1, 17, "e7e8q"),
    "KH-006": ("win", 19, 43, "f4d2"),
    "KH-007": ("draw", 0, 0, "f6f7"),
    "KH-008": ("win", 11, 11, "g3f4"),
    "KH-009": ("draw", 0, 0, "g4g1"),
    "KH-010": ("win", 11, 41, "f5g4"),
    "KH-011": ("draw", 0, None, "b8b4"),
    "KH-012": ("draw", 0, 0, "h8h3"),
    "KH-013": ("draw", 0, 0, "a1d1"),
    "KH2-001": ("draw", 0, 0, "d3d4"),
    "KH2-002": ("draw", 0, None, "f2f4"),
    "KH2-004": ("win", 7, 65, "d4e5"),
    "KH2-005": ("win", 1, 41, "g5h6"),
    "KH2-006": ("win", 5, 39, "d2d6"),
    "KH2-007": ("draw", 0, 0, "a3a4"),
    "KH2-008": ("win", 11, 31, "h2h6"),
    "KH2-010": ("win", 9, 29, "f4f8"),
    "KH2-011": ("win", 13, 33, "h1g2"),
    "KH2-012": ("win", 27, 29, "f4e2"),
}


def default_probe(fen: str) -> dict:
    with urlopen(TABLEBASE_URL + quote(fen), timeout=15) as response:
        return json.load(response)


def white_outcome(category: str, turn: chess.Color) -> str:
    if category == "draw":
        return "draw"
    side_to_move_wins = category in {"win", "cursed-win"}
    winner = turn if side_to_move_wins else not turn
    return "white_win" if winner == chess.WHITE else "black_win"


def verified_tablebase_route(
    board: chess.Board,
    probe: Callable[[str], dict],
    *,
    max_plies: int = 6,
) -> tuple[dict, list[dict]]:
    root = probe(board.fen())
    route = []
    current = board.copy(stack=False)
    payload = root
    for ply in range(1, max_plies + 1):
        moves = payload.get("moves", [])
        if not moves or current.is_game_over(claim_draw=True):
            break
        candidate = moves[0]
        move = chess.Move.from_uci(candidate["uci"])
        if move not in current.legal_moves:
            raise ValueError(f"Tablebase returned illegal move {move.uci()} at ply {ply}")
        san = current.san(move)
        route.append({
            "ply": ply,
            "side": "white" if current.turn == chess.WHITE else "black",
            "uci": move.uci(),
            "san": san,
            "resultingCategoryForOpponent": candidate.get("category"),
            "resultingDtz": candidate.get("dtz"),
        })
        current.push(move)
        if current.is_game_over(claim_draw=True):
            break
        payload = probe(current.fen())
    return root, route


def load_positions() -> list[dict]:
    positions = []
    for path in ADJUDICATIONS:
        positions.extend(json.loads(path.read_text(encoding="utf-8"))["positions"])
    return positions


def build_dataset(
    positions: list[dict],
    probe: Callable[[str], dict] | None = None,
) -> dict:
    def build_case(item: dict) -> dict:
        side, book_claim, source_phrase, page = SOURCE_ASSERTIONS[item["id"]]
        piece_count = len(chess.Board(f"{item['placement']} w - - 0 1").piece_map())
        case = {
            "id": item["id"],
            "file": item["file"],
            "sourcePage": page,
            "placement": item["placement"],
            "sideToMove": side,
            "bookClaim": book_claim,
            "sourcePhrase": source_phrase,
            "pieceCount": piece_count,
            "tablebase": None,
            "verifiedRoute": [],
        }
        if side is None or book_claim is None:
            case["admissionStatus"] = (
                "source_side_unknown" if book_claim is not None else "source_assertion_incomplete"
            )
            return case
        board = chess.Board(f"{item['placement']} {side} - - 0 1")
        if piece_count > 7:
            case["admissionStatus"] = "more_than_seven_pieces_engine_validation_pending"
            return case
        if probe is None:
            audit = TABLEBASE_ROOT_AUDIT.get(item["id"])
            if audit is None:
                case["admissionStatus"] = "tablebase_audit_missing"
                return case
            category, dtz, dtm, best_uci = audit
            best_move = chess.Move.from_uci(best_uci)
            if best_move not in board.legal_moves:
                case["admissionStatus"] = "tablebase_audit_illegal_move"
                return case
            tablebase = {"category": category, "dtz": dtz, "dtm": dtm}
            route = [{
                "ply": 1,
                "side": "white" if board.turn == chess.WHITE else "black",
                "uci": best_uci,
                "san": board.san(best_move),
                "resultingCategoryForOpponent": None,
                "resultingDtz": None,
            }]
            case["verificationMethod"] = "lichess_syzygy_root_audit_2026-08-09"
        else:
            try:
                tablebase, route = verified_tablebase_route(board, probe)
            except Exception as exc:
                case["admissionStatus"] = "tablebase_unavailable"
                case["verificationError"] = f"{type(exc).__name__}: {exc}"
                return case
            case["verificationMethod"] = "live_lichess_syzygy_route"
        computed = white_outcome(tablebase["category"], board.turn)
        case["tablebase"] = {
            "category": tablebase["category"],
            "dtz": tablebase.get("dtz"),
            "dtm": tablebase.get("dtm"),
            "computedWhiteOutcome": computed,
        }
        case["verifiedRoute"] = route
        case["admissionStatus"] = (
            "exact_verified" if computed == book_claim else "book_tablebase_conflict"
        )
        return case

    with ThreadPoolExecutor(max_workers=6) as executor:
        cases = list(executor.map(build_case, positions))
    summary = {}
    for case in cases:
        status = case["admissionStatus"]
        summary[status] = summary.get(status, 0) + 1
    return {
        "schemaVersion": "phase8c-kling-endgame-1.0",
        "sourceTitle": "Chess Studies; or, Endings of Games",
        "sourceYear": 1851,
        "authorityBoundary": (
            "原书结论只属于其精确源局面；七子及以下胜和结果由Syzygy验证。"
            "原书与表库冲突、行棋方不明或超过七子的局面不得作为已验证结果进入产品。"
        ),
        "summary": summary,
        "cases": cases,
    }


def main() -> None:
    dataset = build_dataset(load_positions())
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": dataset["summary"], "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
