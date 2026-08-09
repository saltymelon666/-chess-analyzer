from __future__ import annotations

import json
from pathlib import Path
import sys

import chess
import chess.engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings


DATASET = ROOT / "docs" / "research" / "phase8c-kling-endgame-dataset.json"
DEPTH = 20


def engine_supports_claim(claim: str, white_centipawns: int | None, mate: int | None) -> bool:
    if claim == "draw":
        return mate is None and white_centipawns is not None and abs(white_centipawns) <= 40
    if claim == "white_win":
        return (mate is not None and mate > 0) or (
            mate is None and white_centipawns is not None and white_centipawns >= 200
        )
    if claim == "black_win":
        return (mate is not None and mate < 0) or (
            mate is None and white_centipawns is not None and white_centipawns <= -200
        )
    return False


def verified_pv(board: chess.Board, moves: list[chess.Move], max_plies: int = 10) -> list[dict]:
    current = board.copy(stack=False)
    route = []
    for ply, move in enumerate(moves[:max_plies], 1):
        if move not in current.legal_moves:
            raise ValueError(f"Illegal Stockfish move {move.uci()} at ply {ply}")
        route.append({
            "ply": ply,
            "side": "white" if current.turn == chess.WHITE else "black",
            "uci": move.uci(),
            "san": current.san(move),
        })
        current.push(move)
    return route


def main() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    pending = [
        case for case in payload["cases"]
        if case["admissionStatus"] == "more_than_seven_pieces_engine_validation_pending"
    ]
    settings = load_settings()
    engine = chess.engine.SimpleEngine.popen_uci(str(settings.stockfish_path))
    try:
        for case in pending:
            board = chess.Board(f"{case['placement']} {case['sideToMove']} - - 0 1")
            infos = engine.analyse(board, chess.engine.Limit(depth=DEPTH), multipv=3)
            if isinstance(infos, dict):
                infos = [infos]
            lines = []
            for rank, info in enumerate(infos, 1):
                score = info["score"].pov(chess.WHITE)
                lines.append({
                    "rank": rank,
                    "centipawn": score.score(),
                    "mate": score.mate(),
                    "depth": info.get("depth"),
                    "route": verified_pv(board, info.get("pv", [])),
                })
            best = lines[0]
            supported = engine_supports_claim(
                case["bookClaim"], best["centipawn"], best["mate"]
            )
            case["engineEvidence"] = {
                "engine": "Stockfish",
                "depth": DEPTH,
                "multipv": 3,
                "claimSupported": supported,
                "lines": lines,
            }
            case["admissionStatus"] = (
                "engine_supported_not_tablebase_exact"
                if supported else "book_engine_conflict"
            )
    finally:
        engine.quit()
    summary = {}
    for case in payload["cases"]:
        summary[case["admissionStatus"]] = summary.get(case["admissionStatus"], 0) + 1
    payload["summary"] = summary
    DATASET.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"analysed": len(pending), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
