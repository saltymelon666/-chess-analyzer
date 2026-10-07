import json
from unittest.mock import AsyncMock

import pytest

from app.book_mechanisms import build_book_mechanism
from app.narrative_claims import build_narrative_claim_package
from app.professional_analysis import ChatResult, ProfessionalAnalysisService, compute_professional_complexity
from app.professional_validation import build_validation_context, validate_professional_analysis
from tests.test_book_mechanisms import CASES, _review
from tests.test_professional_analysis import _valid_reference_draft, professional_review


@pytest.mark.asyncio
@pytest.mark.parametrize("fen,san,actual,best,first,second", CASES)
async def test_verified_book_mechanism_returns_strict_prose_without_model_call(
    fen, san, actual, best, first, second,
):
    move = _review(fen, san, actual, best)
    move.allowed_moves = list(dict.fromkeys([move.played_move.san, *move.allowed_moves]))
    mechanism = build_book_mechanism(move)
    assert mechanism is not None
    service = ProfessionalAnalysisService(
        api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1,
    )
    service._chat = AsyncMock(side_effect=AssertionError("redundant model call"))

    result = await service.analyze(move)

    service._chat.assert_not_awaited()
    assert result.usage.attempts == 0
    assert result.validation_warnings == []
    assert first in result.analysis.played_move_analysis.intention
    assert second in result.analysis.played_move_analysis.intention
    assert [phase.moves for phase in result.analysis.played_move_analysis.continuation_phases] == [
        [step.san for step in move.actual_move_line.moves]
    ]
    for rendered, source in zip(result.analysis.candidate_lines, move.candidate_lines):
        assert [phase.moves for phase in rendered.continuation_phases] == [
            [step.san for step in source.moves]
        ]
        assert rendered.continuation_phases[0].evidence_refs == [step.id for step in source.moves]
    context = build_validation_context(move, compute_professional_complexity(move).level)
    assert validate_professional_analysis(
        result.analysis, context, enforce_core_explanation=True,
    ) == []


@pytest.mark.asyncio
async def test_non_mechanism_position_keeps_deepseek_route():
    move = professional_review()
    claims = build_narrative_claim_package(move)
    assert not any(claim.claim_id.endswith(":book-mechanism") for claim in claims.claims)
    draft = _valid_reference_draft(move).model_dump(by_alias=True)
    draft["complexity"] = compute_professional_complexity(move).level
    draft["plans"] = {"white": [], "black": []}
    service = ProfessionalAnalysisService(
        api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1,
    )
    service._chat = AsyncMock(return_value=ChatResult(
        content=json.dumps(draft), prompt_tokens=10, completion_tokens=10,
        total_tokens=20, elapsed_ms=1,
    ))

    result = await service.analyze(move)

    service._chat.assert_awaited_once()
    assert result.usage.attempts == 1
    assert result.validation_warnings == []


@pytest.mark.asyncio
async def test_fast_path_error_falls_back_to_validated_model(monkeypatch):
    move = professional_review()
    draft = _valid_reference_draft(move).model_dump(by_alias=True)
    draft["complexity"] = compute_professional_complexity(move).level
    draft["plans"] = {"white": [], "black": []}
    service = ProfessionalAnalysisService(
        api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1,
    )
    service._chat = AsyncMock(return_value=ChatResult(
        content=json.dumps(draft), prompt_tokens=10, completion_tokens=10,
        total_tokens=20, elapsed_ms=1,
    ))
    monkeypatch.setattr("app.professional_analysis._has_book_mechanism_claim", lambda _package: True)
    monkeypatch.setattr(
        "app.professional_analysis.build_safe_professional_analysis",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected fast path error")),
    )

    result = await service.analyze(move)

    service._chat.assert_awaited_once()
    assert result.usage.attempts == 1
    assert result.validation_warnings == []
