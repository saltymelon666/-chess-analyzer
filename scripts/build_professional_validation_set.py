from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import chess.pgn

from app.config import load_settings
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.professional_analysis import compute_professional_complexity


SELECTORS = [
    ("opening-1", "simple_opening", 1, 5, "后翼兵开局自然发展"),
    ("opening-2", "simple_opening", 2, 5, "侧翼开局准备王翼堡垒"),
    ("opening-3", "simple_opening", 6, 5, "西班牙开局早期发展"),
    ("tactic-1", "direct_tactics", 4, 19, "中心交换后的直接战术"),
    ("tactic-2", "direct_tactics", 5, 51, "王翼突破与强制将军"),
    ("tactic-3", "direct_tactics", 6, 89, "残局中的将军与吃子计算"),
    ("king-attack-1", "king_attack", 2, 91, "推进兵制造升变和王区威胁"),
    ("king-attack-2", "king_attack", 5, 49, "子力靠近王区准备进攻"),
    ("king-attack-3", "king_attack", 6, 71, "封闭局面王翼兵推进"),
    ("center-1", "center_counter", 1, 17, "中心兵突破反击"),
    ("center-2", "center_counter", 5, 25, "中心推进与交换选择"),
    ("closed-1", "closed_center_wing_attack", 3, 19, "封闭中心后的王翼空间争夺"),
    ("closed-2", "closed_center_wing_attack", 6, 29, "封闭中心后的两翼调兵"),
    ("simplify-1", "simplification_endgame", 4, 29, "交换后转入少子局面"),
    ("simplify-2", "simplification_endgame", 1, 67, "后交换并转入马兵残局"),
]

STRATEGY_BY_CATEGORY = {
    "simple_opening": {"white": "完成发展并保持中心控制", "black": "完成发展并准备中心反击"},
    "direct_tactics": {"white": "先计算强制走法和子力安全", "black": "先计算强制回应和反击目标"},
    "king_attack": {"white": "协调进攻子力并检查王区突破", "black": "加固王区并寻找反击节奏"},
    "center_counter": {"white": "准备中心突破并改善子力协调", "black": "挑战中心并利用开放线"},
    "closed_center_wing_attack": {"white": "在合适一翼扩张并保留中心稳定", "black": "在另一翼反击并限制空间"},
    "simplification_endgame": {"white": "比较交换后的王和兵结构", "black": "激活王并争取残局主动"},
}


def load_games(path: Path) -> list[chess.pgn.Game]:
    games = []
    with path.open(encoding="utf-8") as handle:
        while game := chess.pgn.read_game(handle):
            games.append(game)
    return games


def selected_pgn(game: chess.pgn.Game, ply_number: int, fixture_id: str) -> str:
    board = game.board()
    moves = list(game.mainline_moves())
    move = moves[ply_number - 1]
    for previous in moves[: ply_number - 1]:
        board.push(previous)
    selected = chess.pgn.Game()
    selected.headers["Event"] = fixture_id
    selected.headers["Site"] = "game1 fixed quality set"
    selected.setup(board)
    selected.add_variation(move)
    return selected.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))


async def build(source: Path) -> list[dict[str, object]]:
    games = load_games(source)
    settings = load_settings()
    engine = StockfishService(
        settings.stockfish_path,
        depth=10,
        threads=1,
        hash_mb=32,
        multipv=3,
        timeout_seconds=60,
    )
    fixtures = []
    for fixture_id, category, game_number, ply_number, description in SELECTORS:
        game = games[game_number - 1]
        pgn = selected_pgn(game, ply_number, fixture_id)
        review = await analyze_pgn(
            pgn=pgn,
            stockfish=engine,
            analysis_id=f"validation-{fixture_id}",
            depth=10,
            timeout_seconds=90,
            max_plies=2,
        )
        move = review.moves[0]
        complexity = compute_professional_complexity(move)
        played_piece_ref = next(
            item["id"]
            for item in move.position_facts.pieces
            if item["square"] == move.played_move.from_square
        )
        response_piece_ref = None
        if move.actual_move_line and move.actual_move_line.moves:
            response = move.actual_move_line.moves[0]
            response_piece_ref = next(
                (
                    item["id"]
                    for item in move.position_facts.pieces
                    if item["square"] == response.from_square
                ),
                None,
            )
        fixtures.append({
            "id": fixture_id,
            "category": category,
            "source": {
                "file": "game1.pgn",
                "game": game_number,
                "ply": ply_number,
                "description": description,
            },
            "pgn": pgn,
            "fen": move.before_fen,
            "sideToMove": move.side,
            "playedMove": {"san": move.san, "uci": move.uci, "ref": move.played_move.id},
            "complexity": complexity.level,
            "complexityReasons": complexity.reasons,
            "stockfishLines": [
                {
                    "id": line.id,
                    "rank": line.rank,
                    "depth": line.depth,
                    "centipawn": line.centipawn,
                    "mateIn": line.mate_in,
                    "plies": [
                        {
                            "id": ply.id,
                            "side": ply.side,
                            "piece": ply.piece,
                            "san": ply.san,
                            "uci": ply.uci,
                            "from": ply.from_square,
                            "to": ply.to_square,
                            "capture": ply.capture,
                            "check": ply.check,
                        }
                        for ply in line.moves[:10]
                    ],
                }
                for line in move.candidate_lines
            ],
            "expected": {
                "keyPieceRefs": [item for item in [played_piece_ref, response_piece_ref] if item],
                "mainDanger": "必须引用当前事实或Stockfish路线中的具体证据",
                "strategy": STRATEGY_BY_CATEGORY[category],
                "forbiddenConclusions": [
                    "事实包之外的棋子或格子",
                    "Stockfish三条路线之外的候选走法",
                    "没有evidenceRefs的战略结论",
                    "把白方和黑方说反",
                ],
            },
        })
    return fixtures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/professional_validation_positions.json"),
    )
    args = parser.parse_args()
    fixtures = asyncio.run(build(args.source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved {len(fixtures)} positions to {args.output}")


if __name__ == "__main__":
    main()
