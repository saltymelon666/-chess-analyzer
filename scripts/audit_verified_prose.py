"""Inspect the position-specific prose that survives the production fact guard."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chess_facts import build_move_fact_package
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.narrative_claims import NarrativeClaimPackage, compose_verified_core_paragraph
from app.professional_analysis import build_professional_payload, compute_professional_complexity
from app.professional_validation import build_validation_context
from app.strategic_plans import StrategicPlanAnalyzer
from app.threat_analysis import ThreatAnalyzer


async def audit(ids: set[str], depth: int) -> None:
    fixtures = json.loads(
        (ROOT / "tests/fixtures/professional_validation_positions.json").read_text(encoding="utf-8")
    )
    engine = StockfishService(
        ROOT / "stockfish.exe", depth=depth, threads=1, hash_mb=32,
        multipv=3, timeout_seconds=60,
    )
    for fixture in fixtures:
        if ids and fixture["id"] not in ids:
            continue
        review = await analyze_pgn(
            pgn=fixture["pgn"], stockfish=engine,
            analysis_id=f"prose-audit-{fixture['id']}",
            depth=depth, timeout_seconds=90, max_plies=2,
        )
        move = review.moves[0]
        facts = build_move_fact_package(move)
        threats = ThreatAnalyzer().classify(facts)
        plans = StrategicPlanAnalyzer().analyze(
            facts, position_facts=move.position_facts, threat_package=threats,
        )
        payload = build_professional_payload(
            move, compute_professional_complexity(move), set(),
            fact_package=facts, threat_package=threats, plan_package=plans,
        )
        claims = NarrativeClaimPackage.model_validate(payload["narrativeClaims"])
        allowed = build_validation_context(move, compute_professional_complexity(move).level).allowed_evidence_ids
        selected = {item for item in claims.recommended_claim_refs}
        print(f"\n[{fixture['id']}] played={move.played_move.san} best={move.best_move_san} "
              f"score={move.before.evaluation}->{move.after.evaluation} loss={move.centipawn_loss}")
        print(f"plans: {[(plan.side, plan.type, plan.goal, plan.supporting_moves, plan.evidence_route_ids, plan.structural_evidence, plan.confidence) for plan in plans.plans]}")
        print(f"chosen: {[item.claim_id for item in claims.claims if item.claim_id in selected]}")
        print(f"effects: {[(item.claim_id, item.statement) for item in claims.claims if item.kind == 'move_effect']}")
        print(f"invalid refs: {[(item.claim_id, sorted(set(item.evidence_refs) - allowed)) for item in claims.claims if set(item.evidence_refs) - allowed]}")
        print(f"routes: best={[item.san for item in move.candidate_lines[0].moves]} actual={[item.san for item in move.actual_move_line.moves] if move.actual_move_line else []}")
        print(compose_verified_core_paragraph(claims))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="*", default=[])
    parser.add_argument("--depth", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(audit(set(args.ids), args.depth))


if __name__ == "__main__":
    main()
