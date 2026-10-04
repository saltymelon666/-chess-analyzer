import json
from unittest.mock import AsyncMock

import pytest

from app.professional_analysis import ChatResult, ProfessionalAnalysisService, compute_professional_complexity
from app.professional_refs import build_reference_payload, parse_professional_draft, validate_professional_draft
from app.professional_validation import build_validation_context
from tests.test_professional_analysis import professional_review, _valid_reference_draft


def compact_draft(move):
    payload = _valid_reference_draft(move).model_dump(by_alias=True)
    for line in payload["candidateLines"]:
        del line["plyRefs"]
    del payload["playedMoveAnalysis"]["plyRefs"]
    del payload["playedMoveAnalysis"]["strongestReplyRef"]
    return payload


def test_omitted_route_refs_are_copied_exactly_from_verified_catalog():
    move = professional_review()
    catalog = build_reference_payload(move, "normal", [])
    original = _valid_reference_draft(move)
    compact = compact_draft(move)
    raw = json.dumps(compact)
    draft, errors = parse_professional_draft(raw, route_payload=catalog)
    assert errors == []
    assert draft == original
    assert validate_professional_draft(draft, move, build_validation_context(move, "normal")) == []
    assert parse_professional_draft(raw)[0] is None  # legacy parser stays strict


@pytest.mark.parametrize("tamper", ["empty", "reversed", "unknown", "wrong_reply", "null_reply", "wrong_line", "duplicate_line", "wrong_move", "bad_evidence"])
def test_explicit_invalid_references_are_never_repaired(tamper):
    move = professional_review()
    payload = compact_draft(move)
    catalog = build_reference_payload(move, "normal", [])
    line = payload["candidateLines"][0]
    played = payload["playedMoveAnalysis"]
    expected = [p["id"] for p in catalog["lines"][0]["plies"]]
    if tamper == "empty":
        line["plyRefs"] = []
    elif tamper == "reversed":
        line["plyRefs"] = list(reversed(expected))
    elif tamper == "unknown":
        line["plyRefs"] = ["unknown"]
    elif tamper == "wrong_reply":
        played["strongestReplyRef"] = expected[0]
    elif tamper == "null_reply":
        played["strongestReplyRef"] = None
    elif tamper == "wrong_line":
        line["lineRef"] = "unknown"
    elif tamper == "duplicate_line":
        payload["candidateLines"][1]["lineRef"] = line["lineRef"]
    elif tamper == "wrong_move":
        played["moveRef"] = expected[0]
    else:
        line["evidenceRefs"] = ["unknown"]
    draft, errors = parse_professional_draft(json.dumps(payload), route_payload=catalog)
    assert errors or validate_professional_draft(draft, move, build_validation_context(move, "normal"))


@pytest.mark.asyncio
async def test_compact_model_response_passes_full_service_without_retry():
    move = professional_review()
    draft = compact_draft(move)
    draft["complexity"] = compute_professional_complexity(move).level
    draft["plans"] = {"white": [], "black": []}
    service = ProfessionalAnalysisService(api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1)
    service._chat = AsyncMock(return_value=ChatResult(
        content=json.dumps(draft), prompt_tokens=10, completion_tokens=10, total_tokens=20, elapsed_ms=1))
    result = await service.analyze(move)
    assert result.analysis is not None
    assert result.validation_warnings == []
    service._chat.assert_awaited_once()
