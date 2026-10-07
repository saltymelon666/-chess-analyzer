import copy
import json
from unittest.mock import AsyncMock

import chess
import pytest

from app.professional_analysis import (
    ChatResult, ProfessionalAnalysisService, _fit_resolved_analysis_length,
    apply_hard_fact_guard, build_professional_payload, build_safe_professional_analysis,
    compute_professional_complexity, professional_user_prompt,
)
from app.professional_validation import (
    _narrative_length, build_validation_context, validate_professional_analysis,
)
from tests.test_book_mechanisms import _review, _san_line
from tests.test_professional_analysis import _valid_reference_draft, professional_review


def kf8_review():
    move = _review("6k1/pp4pp/4B3/3R4/8/3P2P1/Prn2PKP/8 b - - 2 27",
                   "Kf8", "Rd7 h5 Kf3 a5 d4 Ne1+ Ke3 Ng2+ Ke4 Re2+")
    losing = _san_line(chess.Board(move.before_fen), "Kh8 Rd8#", "line:2")
    losing.rank, losing.mate_in = 2, 1
    move.candidate_lines.append(losing)
    move.allowed_moves.extend(fact.san for fact in losing.moves)
    return move


def test_prompt_deduplicates_only_identical_facts_without_mutation():
    move = professional_review()
    cx = compute_professional_complexity(move)
    payload = build_professional_payload(move, cx, build_validation_context(move, cx.level).allowed_evidence_ids)
    original = copy.deepcopy(payload)
    prompt = professional_user_prompt(payload, cx.level)
    packed = json.JSONDecoder().raw_decode(prompt.split("\n", 1)[1])[0]
    assert payload == original
    assert packed["pos"] == payload["pos"]
    assert packed["focus"]["selectedFacts"] == [f["id"] for f in payload["pos"]["facts"]]
    for key in ("lines", "actual", "chessFacts", "narrativeClaims", "positionInterpretation"):
        assert packed[key] == payload[key]
    assert len(json.dumps(packed, ensure_ascii=False)) < len(json.dumps(payload, ensure_ascii=False))
    payload["focus"]["selectedFacts"] = [{"id": "unique", "description": "仅在选择器存在的事实"}]
    packed = json.JSONDecoder().raw_decode(professional_user_prompt(payload, cx.level).split("\n", 1)[1])[0]
    assert packed["focus"]["selectedFacts"] == payload["focus"]["selectedFacts"]


@pytest.mark.asyncio
async def test_short_complete_kf8_draft_is_accepted_once_without_padding(monkeypatch):
    move = kf8_review()
    cx = compute_professional_complexity(move)
    assert cx.level == "complex"
    # Exercise the model route's concise-output contract independently of the
    # verified mechanism fast path, which has its own focused tests.
    monkeypatch.setattr("app.professional_analysis._has_book_mechanism_claim", lambda _package: False)
    draft = _valid_reference_draft(move)
    draft.complexity = cx.level
    draft.plans.white, draft.plans.black = [], []
    draft.main_danger.level, draft.main_danger.danger_ref = "none", None
    service = ProfessionalAnalysisService(api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1)
    service._chat = AsyncMock(return_value=ChatResult(
        content=draft.model_dump_json(by_alias=True), prompt_tokens=10,
        completion_tokens=10, total_tokens=20, elapsed_ms=1))
    result = await service.analyze(move)
    service._chat.assert_awaited_once()
    assert result.validation_warnings == []
    assert result.usage.attempts == 1
    analysis = result.analysis
    assert _narrative_length(analysis.model_dump(by_alias=True)) < 1000
    assert validate_professional_analysis(analysis, build_validation_context(move, cx.level)) == []
    assert "再看实战" not in analysis.model_dump_json()
    assert "引擎先看" not in analysis.model_dump_json()
    assert _fit_resolved_analysis_length(analysis, move, cx.level) == analysis


@pytest.mark.parametrize("tamper", ["empty_core", "short_core", "single_claim", "bad_evidence", "wrong_route", "wrong_color", "too_long"])
def test_concise_prose_does_not_bypass_content_or_fact_checks(tamper):
    move = kf8_review()
    cx = compute_professional_complexity(move)
    analysis = apply_hard_fact_guard(build_safe_professional_analysis(move, cx), move)
    if tamper == "empty_core":
        analysis.played_move_analysis.intention = ""
    elif tamper == "short_core":
        analysis.played_move_analysis.intention = "局面有优势。"
    elif tamper == "single_claim":
        analysis.played_move_analysis.claim_refs = analysis.played_move_analysis.claim_refs[:1]
    elif tamper == "bad_evidence":
        analysis.comparison.evidence_refs = ["missing:evidence"]
    elif tamper == "wrong_route":
        analysis.candidate_lines[0].first_move = "Kh8"
    elif tamper == "wrong_color":
        analysis.main_danger.description = "白王当前可以从g8走到f8。"
    else:
        analysis.comparison.main_difference = "这是完整的说明。" * 400
    assert validate_professional_analysis(analysis, build_validation_context(move, cx.level))
