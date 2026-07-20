from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import load_settings
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.professional_analysis import build_safe_professional_analysis, compute_professional_complexity
from app.professional_validation import build_validation_context, validate_professional_analysis


async def main() -> None:
    positions = json.loads(
        Path("tests/fixtures/professional_validation_positions.json").read_text(encoding="utf-8")
    )
    settings = load_settings()
    engine = StockfishService(
        settings.stockfish_path,
        depth=10,
        threads=1,
        hash_mb=32,
        multipv=3,
        timeout_seconds=60,
    )
    for position in positions:
        print(f"checking {position['id']}", flush=True)
        review = await analyze_pgn(
            pgn=position["pgn"],
            stockfish=engine,
            analysis_id=f"offline-{position['id']}",
            depth=10,
            timeout_seconds=90,
            max_plies=2,
        )
        move = review.moves[0]
        complexity = compute_professional_complexity(move)
        safe = build_safe_professional_analysis(move, complexity)
        errors = validate_professional_analysis(
            safe,
            build_validation_context(move, complexity.level),
        )
        if errors:
            raise RuntimeError(f"{position['id']}: {errors}")
    print(f"checked {len(positions)} positions", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
