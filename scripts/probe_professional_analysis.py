from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from app.config import load_settings
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.professional_analysis import (
    ProfessionalAnalysisService,
    ProfessionalAttemptDiagnostic,
)
from app.professional_validation import build_validation_context, validate_professional_analysis


async def probe(pgn_path: Path) -> dict[str, object]:
    settings = load_settings()
    if not settings.deepseek_api_key:
        raise RuntimeError("未配置DeepSeek API Key")
    engine = StockfishService(
        settings.stockfish_path,
        depth=10,
        threads=1,
        hash_mb=32,
        multipv=3,
        timeout_seconds=60,
    )
    review = await analyze_pgn(
        pgn=pgn_path.read_text(encoding="utf-8"),
        stockfish=engine,
        analysis_id=f"probe-{pgn_path.stem}",
        depth=10,
        timeout_seconds=90,
        max_plies=2,
    )
    move = review.moves[0]
    service = ProfessionalAnalysisService(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_seconds=settings.deepseek_timeout_seconds,
    )
    diagnostics: list[ProfessionalAttemptDiagnostic] = []
    started = time.perf_counter()
    result = await service.analyze(move, diagnostics=diagnostics)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    final_payload = result.analysis.model_dump(by_alias=True)
    final_text = json.dumps(final_payload, ensure_ascii=False)
    validation_errors = validate_professional_analysis(
        result.analysis,
        build_validation_context(move, result.analysis.complexity),
    )
    return {
        "fixture": str(pgn_path),
        "fen": move.before_fen,
        "playedSan": move.san,
        "model": settings.deepseek_model,
        "acceptedAttempt": next((item.attempt for item in diagnostics if item.accepted), None),
        "fallback": bool(result.validation_warnings),
        "finalValidationErrors": validation_errors,
        "narrativeChineseChars": len(re.findall(r"[\u4e00-\u9fff]", final_text)),
        "candidateLineCount": len(result.analysis.candidate_lines),
        "elapsedMs": elapsed_ms,
        "usage": result.usage.model_dump(),
        "attempts": [
            {
                "attempt": item.attempt,
                "accepted": item.accepted,
                "promptTokens": item.prompt_tokens,
                "completionTokens": item.completion_tokens,
                "totalTokens": item.total_tokens,
                "networkMs": item.network_ms,
                "validationMs": item.validation_ms,
                "postprocessMs": item.postprocess_ms,
                "issues": [
                    {"path": issue.path, "category": issue.category, "message": issue.message}
                    for issue in item.issues
                ],
                "normalizations": [
                    {"path": issue.path, "category": issue.category, "message": issue.message}
                    for issue in item.normalizations
                ],
            }
            for item in diagnostics
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pgn", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.pgn)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
