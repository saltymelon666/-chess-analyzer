from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.config import load_settings
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.professional_analysis import (
    build_safe_professional_analysis,
    build_professional_payload,
    compute_professional_complexity,
    professional_user_prompt,
)
from app.professional_validation import build_validation_context, validate_professional_analysis


async def audit(pgn_path: Path) -> dict[str, object]:
    settings = load_settings()
    engine = StockfishService(
        settings.stockfish_path,
        depth=10,
        threads=1,
        hash_mb=32,
        multipv=3,
        timeout_seconds=60,
    )
    result = await analyze_pgn(
        pgn=pgn_path.read_text(encoding="utf-8"),
        stockfish=engine,
        analysis_id="professional-prompt-audit",
        depth=10,
        timeout_seconds=90,
        max_plies=2,
    )
    move = result.moves[0]
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    payload = build_professional_payload(move, complexity, context.allowed_evidence_ids)
    prompt = professional_user_prompt(payload, complexity.level)
    compact_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    safe = build_safe_professional_analysis(move, complexity)
    safe_text = json.dumps(safe.model_dump(by_alias=True), ensure_ascii=False)
    malformed_contexts = []
    for match in __import__("re").finditer(r"(?<![A-Za-z0-9])([A-Za-z][0-9])(?![A-Za-z0-9])", safe_text):
        value = match.group(1)
        if value[0].lower() != "m" and not __import__("re").fullmatch(r"[a-h][1-8]", value, __import__("re").I):
            malformed_contexts.append(safe_text[max(0, match.start() - 30):match.end() + 30])
    return {
        "fen": move.before_fen,
        "playedSan": move.san,
        "complexity": complexity.level,
        "payloadChars": len(compact_payload),
        "promptChars": len(prompt),
        "pieceCount": len(payload["pos"]["pieces"]),
        "factCount": len(payload["pos"]["facts"]),
        "routePlies": [len(item["plies"]) for item in payload["lines"]],
        "actualPlies": len(payload["actual"]["plies"]),
        "routes": [[ply["san"] for ply in item["plies"]] for item in payload["lines"]],
        "safeValidationErrors": validate_professional_analysis(safe, context),
        "malformedContexts": malformed_contexts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pgn", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(audit(args.pgn)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
