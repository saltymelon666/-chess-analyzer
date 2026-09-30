"""Audit one new position from each of 15 distinct public-domain book games.

The book annotation is a comparison reference, never a source of board facts.
This script exercises the local Stockfish-backed, fact-guarded prose path; it
does not pretend to exercise DeepSeek when no test credential is available.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

import chess
import chess.pgn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chess_facts import build_move_fact_package
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.models import MoveReview
from app.narrative_claims import NarrativeClaimPackage, resolve_narrative_claims
from app.position_facts import extract_position_facts
from app.professional_analysis import (
    _fit_resolved_analysis_length,
    apply_hard_fact_guard,
    build_professional_payload,
    build_safe_professional_analysis,
    compute_professional_complexity,
)
from app.professional_validation import build_validation_context, validate_professional_analysis
from app.strategic_plans import StrategicPlanAnalyzer, StrategicPlanPackage
from app.threat_analysis import ThreatAnalyzer, ThreatPackage
from app.unified_book_knowledge import DEFAULT_UNIFIED_BOOK_DATABASE


def _source_positions(excluded_titles: set[str] | None = None) -> list[dict[str, str]]:
    fixture_path = ROOT / "tests/fixtures/professional_validation_positions.json"
    old_fens = {
        item["fen"].split(" ", 4)[0]
        for item in json.loads(fixture_path.read_text(encoding="utf-8"))
        if item.get("fen")
    }
    with sqlite3.connect(DEFAULT_UNIFIED_BOOK_DATABASE) as connection:
        rows = connection.execute(
            """SELECT record_id, source_id, phase, title, locator, fen, move_uci, text_en
               FROM knowledge_records
               WHERE record_type='exact_position' AND fen IS NOT NULL
                 AND move_uci IS NOT NULL AND length(text_en) BETWEEN 80 AND 700
               ORDER BY phase, source_id, title, record_id"""
        ).fetchall()
    eligible: list[dict[str, str]] = []
    for record_id, source_id, phase, title, locator, fen, move_uci, annotation in rows:
        if excluded_titles and title in excluded_titles:
            continue
        if fen.split(" ", 4)[0] in old_fens:
            continue
        try:
            board = chess.Board(fen)
            played = chess.Move.from_uci(move_uci)
        except ValueError:
            continue
        if played not in board.legal_moves or board.fullmove_number < 7:
            continue
        # Keep comments with at least a stated chess mechanism, rather than
        # applause or a result-only note. This is a text-selection rule only.
        if not re.search(
            r"\b(because|therefore|threat|attack|defen[cs]|pawn|knight|bishop|rook|queen|king|exchange|file|square|centre|center)\b",
            annotation,
            flags=re.IGNORECASE,
        ):
            continue
        eligible.append({
            "recordId": record_id, "sourceId": source_id, "phase": phase,
            "title": title, "locator": locator, "fen": fen,
            "moveUci": move_uci, "moveSan": board.san(played),
            "bookExcerpt": annotation.strip(),
        })
    selected: list[dict[str, str]] = []
    used_games: set[tuple[str, str]] = set()
    for phase in ("opening", "middlegame", "endgame"):
        phase_rows = [item for item in eligible if item["phase"] == phase]
        sources = sorted({item["sourceId"] for item in phase_rows})
        for source_id in sources:
            choice = next((
                item for item in phase_rows
                if item["sourceId"] == source_id
                and (item["sourceId"], item["title"]) not in used_games
            ), None)
            if choice is None:
                continue
            selected.append(choice)
            used_games.add((choice["sourceId"], choice["title"]))
            if sum(item["phase"] == phase for item in selected) == 5:
                break
    if len(selected) != 15 or len(used_games) != 15:
        raise RuntimeError(f"Need 15 distinct book games, found {len(selected)}")
    return selected


def _single_move_pgn(item: dict[str, str]) -> str:
    board = chess.Board(item["fen"])
    game = chess.pgn.Game()
    game.setup(board)
    game.headers["Event"] = item["title"]
    game.headers["Source"] = item["locator"]
    game.add_main_variation(chess.Move.from_uci(item["moveUci"]))
    return str(game)


async def audit(depth: int, *, snapshots: Path | None = None, replay: Path | None = None,
                excluded_titles: set[str] | None = None,
                refresh_position_facts: bool = False) -> dict[str, object]:
    engine = StockfishService(
        ROOT / "stockfish.exe", depth=depth, threads=1, hash_mb=32,
        multipv=3, timeout_seconds=60,
    )
    results: list[dict[str, object]] = []
    saved = json.loads(replay.read_text(encoding="utf-8")) if replay else []
    if replay:
        depths = {row["depth"] for row in saved}
        if len(depths) != 1:
            raise ValueError("Replay snapshots must use one recorded engine depth")
        depth = depths.pop()
    recorded: list[dict[str, object]] = []
    for index, item in enumerate(_source_positions(excluded_titles), 1):
        print(f"[{index}/15] {item['phase']} {item['title']} {item['moveSan']}", flush=True)
        if replay:
            snapshot = next(row for row in saved if row["recordId"] == item["recordId"])
            move = MoveReview.model_validate(snapshot["move"])
            if move.before_fen != item["fen"] or move.played_move.uci != item["moveUci"]:
                raise ValueError("Replay snapshot does not match the selected position")
            threats = ThreatPackage.model_validate(snapshot["threats"])
            plans = StrategicPlanPackage.model_validate(snapshot["plans"])
        else:
            review = await analyze_pgn(
                pgn=_single_move_pgn(item), stockfish=engine,
                analysis_id=f"fresh-book-{index}", depth=depth,
                timeout_seconds=90, max_plies=2,
            )
            move = review.moves[0]
            facts = build_move_fact_package(move)
            threats = await ThreatAnalyzer().analyze(facts, stockfish=engine)
            plans = StrategicPlanAnalyzer().analyze(
                facts, position_facts=move.position_facts, threat_package=threats,
            )
        if refresh_position_facts:
            move.position_facts = extract_position_facts(
                move.before_fen, candidate_lines=move.candidate_lines,
                actual_move_line=move.actual_move_line, tactics=move.verified_tactics,
                namespace=f"move-{move.index}-before",
            )
            move.position_facts_after = extract_position_facts(
                move.after_fen, candidate_lines=[], actual_move_line=move.actual_move_line,
                tactics=move.verified_tactics, namespace=f"move-{move.index}-after",
            )
            for line in [*move.candidate_lines, *([move.actual_move_line] if move.actual_move_line else [])]:
                namespace = "line-played-result" if line.id == "line:played" else f"line-{line.rank}-result"
                line.resulting_position_facts = extract_position_facts(
                    line.resulting_fen, candidate_lines=[], actual_move_line=None,
                    tactics=[], namespace=namespace,
                )
        recorded.append({"recordId": item["recordId"], "depth": depth,
                         "move": move.model_dump(), "threats": threats.model_dump(),
                         "plans": plans.model_dump()})
        if snapshots:
            snapshots.parent.mkdir(parents=True, exist_ok=True)
            snapshots.write_text(json.dumps(recorded, ensure_ascii=False, indent=2), encoding="utf-8")
        complexity = compute_professional_complexity(move)
        context = build_validation_context(move, complexity.level)
        facts = build_move_fact_package(move)
        payload = build_professional_payload(
            move, complexity, context.allowed_evidence_ids,
            fact_package=facts, threat_package=threats, plan_package=plans,
        )
        claims = NarrativeClaimPackage.model_validate(payload["narrativeClaims"])
        safe = build_safe_professional_analysis(move, complexity)
        guarded = apply_hard_fact_guard(safe, move, narrative_claims=claims)
        fitted = _fit_resolved_analysis_length(guarded, move, complexity.level)
        selected = resolve_narrative_claims(claims, fitted.played_move_analysis.claim_refs)
        invalid_refs = sorted({
            ref for claim in selected for ref in claim.evidence_refs
            if ref not in context.allowed_evidence_ids
        })
        core = fitted.played_move_analysis.intention
        row = {
            **item,
            "bestMoveSan": move.best_move_san,
            "beforeEvaluation": move.before.evaluation,
            "afterEvaluation": move.after.evaluation,
            "centipawnLoss": move.centipawn_loss,
            "grade": move.quality_label,
            "core": core,
            "analysis": fitted.model_dump(by_alias=True),
            "selectedClaimKinds": [claim.kind for claim in selected],
            "mechanismStatements": [claim.statement for claim in selected
                                    if claim.kind in {"verified_choice", "verified_consequence", "verified_plan", "position_cause"}],
            "invalidEvidenceRefs": invalid_refs,
            "validationErrors": validate_professional_analysis(fitted, context),
            "bookExcerptIsFactSource": False,
        }
        results.append(row)
    return {
        "scope": "15 distinct public-domain book games; one position per game; local Stockfish and safe prose, no DeepSeek",
        "depth": depth,
        "positionFactsRecomputed": refresh_position_facts,
        "positions": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--depth", type=int, default=10)
    parser.add_argument("--results", type=Path, default=ROOT / "work/fresh-book-15/results.json")
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--exclude-results", type=Path)
    parser.add_argument("--refresh-position-facts", action="store_true",
                        help="Recompute python-chess facts from unchanged FENs; retain engine scores and routes")
    args = parser.parse_args()
    excluded_titles = {
        row["title"] for row in json.loads(args.exclude_results.read_text(encoding="utf-8"))["positions"]
    } if args.exclude_results else None
    result = asyncio.run(audit(args.depth, snapshots=args.snapshots, replay=args.replay,
                              excluded_titles=excluded_titles,
                              refresh_position_facts=args.refresh_position_facts))
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = result["positions"]
    if args.report:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["positions"] if args.baseline else []
        previous = {row["recordId"]: row for row in baseline}
        report = ["# 新15盘：正文逐篇对照", "",
                  f"本地 Stockfish 深度 {result['depth']}，每盘一个决策局面。正文为程序实际输出，未人工改写。",
                  "引擎评分与路线保持不变；本报告验证文字生成规则，不代表线上 DeepSeek 已完成验收。",
                  "python-chess 局面事实：" + ("按原 FEN 重新提取。" if args.refresh_position_facts else "沿用采集版本。"), ""]
        for index, row in enumerate(rows, 1):
            report.extend([f"## {index}. {row['title']} · {row['moveSan']}", ""])
            if row["recordId"] in previous:
                report.extend(["原文：", "", previous[row["recordId"]]["core"], ""])
            report.extend(["新版原文：", "", row["core"], "",
                           "严格校验：" + ("通过" if not row["validationErrors"] else "；".join(row["validationErrors"])), ""])
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("\n".join(report), encoding="utf-8")
    print(f"Audited {len(rows)} distinct games; invalid refs: "
          f"{sum(bool(row['invalidEvidenceRefs']) for row in rows)}; "
          f"validation failures: {sum(bool(row['validationErrors']) for row in rows)}")
    print(f"Results: {args.results}")
    if any(row["invalidEvidenceRefs"] or row["validationErrors"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
