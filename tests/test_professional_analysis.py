import asyncio
import json
from unittest.mock import AsyncMock

import chess
from fastapi.testclient import TestClient
import httpx
import pytest

from app import api
from app.game_review import _move_facts, _variation_moves
from app.models import (
    CandidateLine,
    ComplexityFactors,
    EvaluationSnapshot,
    GeneratedProfessionalAnalysis,
    EvidenceFact,
    MoveFacts,
    MoveReview,
    ProfessionalComplexity,
    ProfessionalAnalysisUsage,
    ProfessionalAnalysisDraft,
    ProfessionalEvidenceText,
    VerifiedTactic,
)
from app.position_facts import extract_position_facts
from app.analysis_focus import select_analysis_focus
from app.chess_facts import build_move_fact_package
from app.professional_analysis import (
    ChatResult,
    PROFESSIONAL_PROMPT_VERSION,
    PROFESSIONAL_TOKEN_LIMITS,
    ProfessionalAnalysisService,
    apply_hard_fact_guard,
    build_professional_payload,
    build_safe_professional_analysis,
    compute_professional_complexity,
    professional_cache_key,
    professional_system_prompt,
    professional_user_prompt,
    _trim_to_complete_sentence,
)
from app.professional_validation import (
    LENGTH_RANGES,
    _narrative_length,
    build_validation_context,
    normalize_program_owned_claims,
    parse_professional_analysis,
    validate_professional_analysis,
)
from app.professional_refs import (
    _complete_display_sentence,
    _program_direct_purpose,
    build_reference_payload,
    normalize_professional_draft_literals,
    resolve_professional_draft,
    validate_professional_draft,
)
from app.strategic_plans import StrategicPlanAnalyzer
from app.threat_analysis import ThreatFact, ThreatIgnoreTest, ThreatPackage, position_id


def _line(board: chess.Board, rank: int, uci_moves: list[str], line_id: str) -> CandidateLine:
    current = board.copy(stack=False)
    facts = []
    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        facts.append(_move_facts(current, move))
        current.push(move)
    moves, resulting_fen = _variation_moves(board, facts)
    for item in moves:
        item.id = f"{line_id}:ply:{item.ply}"
    line = CandidateLine(
        id=line_id,
        rank=rank,
        depth=18,
        evaluation=30 - rank * 10,
        mate=None,
        firstMove=facts[0],
        pv=moves,
        resultingFen=resulting_fen,
    )
    line.resulting_position_facts = extract_position_facts(
        resulting_fen,
        candidate_lines=[],
        actual_move_line=None,
        tactics=[],
        namespace=f"{line_id.replace(':', '-')}-result",
    )
    return line


def professional_review() -> MoveReview:
    board = chess.Board()
    played = _move_facts(board, chess.Move.from_uci("e2e4"))
    played.id = "move:played:1"
    after = board.copy(stack=False)
    after.push_uci("e2e4")
    candidates = [
        _line(board, 1, ["e2e4", "e7e5", "g1f3", "b8c6"], "line:1"),
        _line(board, 2, ["d2d4", "d7d5", "g1f3", "g8f6"], "line:2"),
        _line(board, 3, ["g1f3", "g8f6", "d2d4", "d7d5"], "line:3"),
    ]
    actual = _line(after, 1, ["e7e5", "g1f3", "b8c6", "f1c4"], "line:played")
    before_facts = extract_position_facts(
        board.fen(), candidate_lines=candidates, actual_move_line=actual, tactics=[], namespace="move-1-before"
    )
    after_facts = extract_position_facts(
        after.fen(), candidate_lines=[], actual_move_line=actual, tactics=[], namespace="move-1-after"
    )
    allowed_squares = sorted(
        {piece["square"] for piece in before_facts.pieces}
        | {square for line in [*candidates, actual] for item in line.moves for square in (item.from_square, item.to_square)}
    )
    allowed_moves = sorted(
        {played.san, played.uci}
        | {value for line in [*candidates, actual] for item in line.moves for value in (item.san, item.uci)}
    )
    return MoveReview(
        index=1,
        move_number=1,
        notation="1.e4",
        side="white",
        san=played.san,
        uci=played.uci,
        from_square=played.from_square,
        to_square=played.to_square,
        before_fen=board.fen(),
        after_fen=after.fen(),
        before=EvaluationSnapshot(evaluation="+0.30", centipawn=30),
        after=EvaluationSnapshot(evaluation="+0.10", centipawn=10),
        played_move=played,
        best_move=candidates[0].first_move,
        opponent_reply=actual.first_move,
        centipawn_loss=20,
        best_move_uci="e2e4",
        best_move_san="e4",
        best_pv=[item.san for item in candidates[0].moves],
        quality_key="best",
        quality_symbol="!",
        quality_label="最佳着",
        mate_involved=False,
        only_legal_move=False,
        principal_variation=[item.san for item in candidates[0].moves],
        complexity="normal",
        complexity_factors=ComplexityFactors(
            legal_move_count=20,
            candidate_gap_cp=10,
            only_reasonable_move=False,
            pv_length=4,
            evaluation_swing_cp=20,
            forcing_line_plies=0,
            engaged_piece_count=0,
        ),
        verified_facts=["白方实战走了e4。"],
        allowed_squares=allowed_squares,
        allowed_moves=allowed_moves,
        pieces_before={piece["square"]: f"{piece['side']}_{piece['piece']}" for piece in before_facts.pieces},
        candidate_lines=candidates,
        actual_move_line=actual,
        position_facts=before_facts,
        position_facts_after=after_facts,
    )


def test_stable_evidence_ids_and_cache_key_cover_routes_and_prompt_version() -> None:
    move = professional_review()
    context = build_validation_context(move, compute_professional_complexity(move).level)
    assert "line:1" in context.allowed_evidence_ids
    assert "line:1:ply:1" in context.allowed_evidence_ids
    assert any(item.startswith("piece:move-1-before:white-knight-") for item in context.allowed_evidence_ids)
    assert any(item.startswith("fact:move-1-before:king:") for item in context.allowed_evidence_ids)
    first = professional_cache_key(move, stockfish_version="Stockfish 18", stockfish_depth=18)
    second = professional_cache_key(move, stockfish_version="Stockfish 18", stockfish_depth=18)
    assert first == second
    assert PROFESSIONAL_PROMPT_VERSION
    move.candidate_lines[0].moves[0].uci = "a2a3"
    assert professional_cache_key(move, stockfish_version="Stockfish 18", stockfish_depth=18) != first


def test_production_professional_draft_cannot_generate_strategic_plans() -> None:
    move = professional_review()
    context = build_validation_context(move, "normal")
    draft = _valid_reference_draft(move)
    facts = build_move_fact_package(move)
    strategic = StrategicPlanAnalyzer().analyze(
        facts,
        position_facts=move.position_facts,
    )

    issues = validate_professional_draft(
        draft,
        move,
        context,
        strategic_plan_package=strategic,
    )

    assert any(issue.path == "plans" for issue in issues)


def test_validator_rejects_hallucinated_evidence_square_and_route() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    payload = build_safe_professional_analysis(move, complexity).model_dump(by_alias=True)
    payload["mainDanger"]["description"] = "黑后在h9攻击白王，白方会立即受损。"
    payload["mainDanger"]["sideInDanger"] = "white"
    payload["mainDanger"]["evidenceRefs"] = ["fact:not-real"]
    payload["playedMoveAnalysis"]["move"] = "Qh5"
    payload["playedMoveAnalysis"]["positiveEffects"] = ["实战走法吃掉了黑后。"]
    payload["candidateLines"][0]["firstMove"] = "Qh5"
    payload["candidateLines"][0]["continuationPhases"][0]["moves"] = ["d4"]
    from app.models import ProfessionalAnalysis
    analysis = ProfessionalAnalysis.model_validate(payload)
    errors = validate_professional_analysis(analysis, context, enforce_length=False)
    assert any("evidenceRefs" in error for error in errors)
    assert any("棋盘范围外" in error for error in errors)
    assert any("实际走法" in error for error in errors)
    assert any("未验证的吃子" in error for error in errors)
    assert any("firstMove" in error for error in errors)
    assert any("候选路线1引用了" in error for error in errors)


def test_validator_prevents_fourth_line_and_empty_vague_claim() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    payload = build_safe_professional_analysis(move, complexity).model_dump(by_alias=True)
    fourth = dict(payload["candidateLines"][0])
    fourth["rank"] = 3
    payload["candidateLines"].append(fourth)
    from app.professional_validation import parse_professional_analysis
    parsed, parse_errors = parse_professional_analysis(__import__("json").dumps(payload, ensure_ascii=False))
    assert parsed is None
    assert parse_errors

    payload["candidateLines"].pop()
    payload["plans"]["white"][0]["description"] = "白方准备进攻。"
    from app.models import ProfessionalAnalysis
    analysis = ProfessionalAnalysis.model_validate(payload)
    errors = validate_professional_analysis(analysis, context, enforce_length=False)
    assert any("准备进攻" in error for error in errors)


@pytest.mark.asyncio
async def test_two_invalid_deepseek_results_retry_once_then_use_safe_analysis() -> None:
    move = professional_review()
    service = ProfessionalAnalysisService(
        api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1
    )
    service._chat = AsyncMock(
        return_value=ChatResult(
            content='{"not":"the schema"}',
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            elapsed_ms=20,
        )
    )
    result = await service.analyze(move)
    context = build_validation_context(move, result.analysis.complexity)
    assert validate_professional_analysis(result.analysis, context, enforce_length=False) == []
    assert service._chat.await_count == 2
    assert result.usage.attempts == 2
    assert result.usage.total_tokens == 30
    assert result.validation_warnings


def test_complexity_profiles_have_distinct_lengths_and_token_limits() -> None:
    assert LENGTH_RANGES["simple"][1] < LENGTH_RANGES["normal"][0]
    assert LENGTH_RANGES["normal"][1] < LENGTH_RANGES["complex"][0]
    assert PROFESSIONAL_TOKEN_LIMITS["simple"] < PROFESSIONAL_TOKEN_LIMITS["normal"] < PROFESSIONAL_TOKEN_LIMITS["complex"]


def test_professional_api_cache_uses_fact_hash_without_second_service_call(monkeypatch) -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    generated = GeneratedProfessionalAnalysis(
        analysis=build_safe_professional_analysis(move, complexity),
        complexity_reasons=complexity.reasons,
        usage=ProfessionalAnalysisUsage(elapsed_ms=10, attempts=1),
    )

    class FakeProfessionalService:
        def __init__(self):
            self.calls = 0

        async def analyze(self, selected, *, threat_package=None):
            assert threat_package is not None
            self.calls += 1
            await asyncio.sleep(0)
            return generated

    fake = FakeProfessionalService()
    monkeypatch.setattr(api, "professional_service", fake)
    api.game_cache.clear()
    api.professional_cache.clear()
    api.professional_tasks.clear()
    analysis_id = "professional-cache-test"
    api.game_cache[analysis_id] = [move]
    client = TestClient(api.app)
    first = client.post("/api/professional-analysis", json={"analysis_id": analysis_id, "move_index": 1})
    second = client.post("/api/professional-analysis", json={"analysis_id": analysis_id, "move_index": 1})
    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert "keyPieces" not in first.json()["analysis"]
    position = first.json()["analysis"]["positionAssessment"]
    assert "material" not in position
    if position["kingSafety"]["isRelevant"] is False:
        assert "white" not in position["kingSafety"]
        assert "black" not in position["kingSafety"]
    assert second.json()["cached"] is True
    assert fake.calls == 1


def test_compact_prompt_keeps_complete_contract_without_full_pydantic_schema() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    prompt = professional_user_prompt(
        build_professional_payload(move, complexity, context.allowed_evidence_ids),
        complexity.level,
    )
    assert len(prompt) < 30_000
    assert '"v":"professional-v6-focus"' in prompt
    assert '"selectedFacts"' in prompt
    assert '"material"' not in prompt
    assert '"playedMoveAnalysis"' in prompt
    assert '"candidateLines"' in prompt
    assert '"keyPieces"' not in prompt
    assert '"pieceRef"' not in prompt
    assert "keyPieces" not in json.dumps(
        ProfessionalAnalysisDraft.model_json_schema(by_alias=True)
    )
    assert '"lineRef"' in prompt
    assert '"plyRefs"' in prompt
    assert '"positionAfter"' not in prompt
    assert '"allowedEvidenceIds"' not in prompt
    assert '"allowedMoves"' not in prompt
    assert '"chessFacts"' in prompt
    assert '"verified":true' in prompt
    assert '"uci"' not in prompt
    assert move.before_fen not in prompt
    assert "$defs" not in prompt


def test_prompt_locks_program_confirmed_opening_identity() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    payload = build_professional_payload(move, complexity, context.allowed_evidence_ids)
    payload["confirmedOpening"] = {
        "identityAuthority": "program_confirmed",
        "name": "Italian Game: Giuoco Piano",
        "eco": "C50",
        "variationPath": ["Giuoco Piano"],
    }

    prompt = professional_user_prompt(payload, complexity.level)

    assert "confirmedOpening的名称、ECO和变例已经由程序锁定" in prompt
    assert "不得重新识别或输出其他名称" in prompt


def _valid_reference_draft(move: MoveReview) -> ProfessionalAnalysisDraft:
    payload = build_reference_payload(move, "normal", ["fixed test"])
    facts = payload["pos"]["facts"]
    routes = payload["lines"]
    actual = payload["actual"]["plies"]

    fallback_ref = routes[0]["id"]

    def side_fact(side: str) -> str:
        return next(
            (
                item["id"]
                for item in facts
                if item.get("side") in {side, "neutral", None}
            ),
            fallback_ref,
        )

    def side_ply(side: str) -> str:
        return next(
            ply["id"]
            for route in routes
            for ply in route["plies"]
            if ply["side"] == side
        )

    danger_ply = routes[0]["plies"][0]

    return ProfessionalAnalysisDraft.model_validate(
        {
            "complexity": "normal",
            "positionAssessment": {
                "summary": "局面保持平衡，双方都需要先完成发展。",
            },
            "mainDanger": {
                "level": "short_term",
                "dangerRef": danger_ply["id"],
                "explanation": "首要危险来自候选路线第一步造成的节奏变化。",
                "consequence": "若忽略这个变化，对手会改善子力位置。",
                "evidenceRefs": [danger_ply["id"]],
            },
            "plans": {
                "white": [
                    {
                        "strategyTag": "center_control",
                        "explanation": "白方先改善子力协调，再争取主动。",
                        "requiredPreparation": "减少未发展子力并保持中心控制。",
                        "evidenceRefs": [side_ply("white")],
                    }
                ],
                "black": [
                    {
                        "strategyTag": "center_control",
                        "explanation": "黑方先完成发展，再寻找反击时机。",
                        "requiredPreparation": "让子力互相保护并准备争夺中心。",
                        "evidenceRefs": [side_ply("black")],
                    }
                ],
            },
            "playedMoveAnalysis": {
                "moveRef": payload["played"]["ref"],
                "intention": "实战走法合理地推进了当前计划。",
                "positiveEffects": ["它改善了白方的中心影响力。"],
                "problems": ["仍需注意对手的直接反击。"],
                "strongestReplyRef": actual[0]["id"],
                "plyRefs": [item["id"] for item in actual],
                "continuationExplanation": "双方围绕发展和中心继续调整。",
                "errorType": "none",
                "evidenceRefs": [payload["played"]["ref"]],
            },
            "candidateLines": [
                {
                    "lineRef": route["id"],
                    "strategyTags": ["center_control"],
                    "directPurpose": "这条路线用自然发展保持局面稳定。",
                    "advantages": ["行动顺序清楚并有事实路线支持。"],
                    "risks": ["仍要检查对手下一步的强制回应。"],
                    "plyRefs": [item["id"] for item in route["plies"]],
                    "continuationExplanation": "路线展示了双方最直接的应对顺序。",
                    "evidenceRefs": [route["id"]],
                }
                for route in routes
            ],
            "comparison": {
                "mainDifference": "最佳路线更快改善协调，实战路线保留了更多变化。",
                "whyFirstLineIsBest": "第一路线减少了对手反击并保持行动连续。",
                "evidenceRefs": [routes[0]["id"], payload["actual"]["id"]],
            },
        }
    )
def test_reference_draft_resolves_ids_without_model_generated_board_literals() -> None:
    move = professional_review()
    draft = _valid_reference_draft(move)
    context = build_validation_context(move, "normal")
    assert validate_professional_draft(draft, move, context) == []

    resolved = resolve_professional_draft(draft, move, context)
    assert validate_professional_analysis(resolved, context, enforce_length=False) == []
    assert "keyPieces" not in resolved.model_dump(by_alias=True)
    assert [item.rank for item in resolved.candidate_lines] == [1, 2, 3]
    assert resolved.candidate_lines[0].first_move == move.candidate_lines[0].first_move.san
    assert resolved.candidate_lines[0].direct_purpose == "第一步白兵从e2走到e4（e4），作为这条Stockfish路线的起点。"
    assert resolved.candidate_lines[0].direct_purpose != draft.candidate_lines[0].direct_purpose


def test_program_direct_purpose_uses_only_verified_first_ply_events() -> None:
    board = chess.Board("4k3/8/8/8/8/8/4r3/4R1K1 w - - 0 1")
    route = _line(board, 1, ["e1e2", "e8f7"], "line:capture-check")

    assert _program_direct_purpose(route) == "第一步白车从e1走到e2（Rxe2+），并吃掉车、形成将军。"


def test_incomplete_display_text_is_dropped_instead_of_kept_as_residue() -> None:
    assert _complete_display_sentence("当前局面为意大。") == ""
    assert _complete_display_sentence("黑象(e7)正。") == ""
    assert _complete_display_sentence("黑象目前位于e7。") == "黑象目前位于e7。"


def test_length_fitting_only_trims_at_complete_sentence_boundary() -> None:
    text = "第一句内容完整。第二句正在继续说明，但还没有结束。"
    shortened = _trim_to_complete_sentence(text, 18)
    assert shortened == "第一句内容完整。"
    assert not shortened.endswith(("正在", "准备", "意大", "正"))


def test_reference_resolver_rebuilds_incomplete_summary() -> None:
    move = professional_review()
    payload = _valid_reference_draft(move).model_dump(by_alias=True)
    payload["positionAssessment"]["summary"] = "当前局面为意大。"
    draft = ProfessionalAnalysisDraft.model_validate(payload)

    resolved = resolve_professional_draft(draft, move, build_validation_context(move, "normal"))

    assert "意大" not in resolved.position_assessment.summary


def test_reference_resolver_removes_unverified_event_words_before_final_validation() -> None:
    move = professional_review()
    payload = _valid_reference_draft(move).model_dump(by_alias=True)
    payload["plans"]["white"][0]["explanation"] = "准备将杀并通过吃子扩大优势。"
    draft = ProfessionalAnalysisDraft.model_validate(payload)
    context = build_validation_context(move, "normal")

    resolved = resolve_professional_draft(draft, move, context)
    assert "将杀" not in resolved.plans.white[0].description
    assert "吃子" not in resolved.plans.white[0].description
    assert validate_professional_analysis(resolved, context, enforce_length=False) == []


def test_reference_draft_reports_precise_paths_for_invalid_refs() -> None:
    move = professional_review()
    payload = _valid_reference_draft(move).model_dump(by_alias=True)
    payload["candidateLines"][1]["lineRef"] = "line:not-real"
    payload["plans"]["black"][0]["evidenceRefs"] = [
        next(
            item.id
            for line in move.candidate_lines
            for item in line.moves
            if item.side == "white"
        )
    ]
    draft = ProfessionalAnalysisDraft.model_validate(payload)
    issues = validate_professional_draft(draft, move, build_validation_context(move, "normal"))

    assert any(issue.path == "candidateLines[1].lineRef" and issue.category == "不属于Stockfish的走法" for issue in issues)
    assert any(issue.path == "plans.black[0].evidenceRefs" and issue.category == "黑白说反" for issue in issues)


def test_resolved_validation_errors_keep_their_field_path() -> None:
    from app.professional_analysis import _resolved_validation_issue

    issue = _resolved_validation_issue("mainDanger.description: 没有同时指出具体棋子和格子")
    assert issue.path == "mainDanger.description"
    assert issue.message == "没有同时指出具体棋子和格子"


def test_reference_draft_allows_only_board_literals_already_present_in_facts() -> None:
    move = professional_review()
    context = build_validation_context(move, "normal")
    payload = _valid_reference_draft(move).model_dump(by_alias=True)
    payload["positionAssessment"]["summary"] += " 已有事实中的d4可以被复述。"
    allowed = ProfessionalAnalysisDraft.model_validate(payload)
    assert not any(
        issue.category in {"不存在的格子", "不属于Stockfish的走法"}
        for issue in validate_professional_draft(allowed, move, context)
    )

    payload["positionAssessment"]["summary"] += " 但z9和Qh5不是有效事实。"
    invalid = ProfessionalAnalysisDraft.model_validate(payload)
    issues = validate_professional_draft(invalid, move, context)
    assert any(issue.path == "positionAssessment.summary" and "z9" in issue.message for issue in issues)
    assert any(issue.path == "positionAssessment.summary" and "Qh5" in issue.message for issue in issues)


def test_reference_literal_normalizer_removes_hallucinated_board_tokens_then_revalidates() -> None:
    move = professional_review()
    context = build_validation_context(move, "normal")
    payload = _valid_reference_draft(move).model_dump(by_alias=True)
    payload["plans"]["white"][0]["explanation"] = "计划把子力放到z9并走Qh5或e2e5。"
    draft = ProfessionalAnalysisDraft.model_validate(payload)

    normalized, changes = normalize_professional_draft_literals(draft, move, context)
    assert {item.path for item in changes} == {"plans.white[0].explanation"}
    assert "z9" not in normalized.plans.white[0].explanation
    assert "Qh5" not in normalized.plans.white[0].explanation
    assert "e2e5" not in normalized.plans.white[0].explanation
    assert validate_professional_draft(normalized, move, context) == []


def test_reference_payload_deduplicates_facts_and_excludes_internal_fields() -> None:
    move = professional_review()
    payload = build_reference_payload(move, "normal", ["fixed test"])
    serialized = json.dumps(payload, ensure_ascii=False)
    fact_ids = [item["id"] for item in payload["pos"]["facts"]]

    assert len(fact_ids) == len(set(fact_ids))
    assert "positionAfter" not in serialized
    assert "allowedEvidenceIds" not in serialized
    assert "legalMoves" not in serialized
    assert '"uci"' not in serialized
    assert all(len(line["plies"]) <= 10 for line in payload["lines"])


@pytest.mark.asyncio
async def test_retry_reports_compact_errors_without_echoing_untrusted_output() -> None:
    move = professional_review()
    marker = "UNTRUSTED_OUTPUT_MUST_NOT_BE_ECHOED"
    service = ProfessionalAnalysisService(
        api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1
    )
    service._chat = AsyncMock(
        return_value=ChatResult(
            content=json.dumps({"not": "the schema", "marker": marker}),
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            elapsed_ms=20,
        )
    )
    result = await service.analyze(move)
    retry_prompt = service._chat.await_args_list[1].kwargs["prompt"]
    assert marker not in retry_prompt
    assert "上一次返回未通过程序校验" in retry_prompt
    assert result.validation_warnings


@pytest.mark.asyncio
async def test_professional_network_failure_uses_validated_safe_fallback() -> None:
    move = professional_review()
    service = ProfessionalAnalysisService(
        api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1
    )
    service._chat = AsyncMock(side_effect=httpx.ConnectError("offline"))

    result = await service.analyze(move)

    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    assert validate_professional_analysis(result.analysis, context) == []
    assert any("暂不可用" in warning for warning in result.validation_warnings)


@pytest.mark.asyncio
async def test_professional_chat_explicitly_disables_thinking_mode(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url, *, headers, json):
            captured.update(json)
            return FakeResponse()

    monkeypatch.setattr("app.professional_analysis.httpx.AsyncClient", lambda timeout: FakeClient())
    service = ProfessionalAnalysisService(
        api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1
    )
    await service._chat(system="system", prompt="json", max_tokens=100, temperature=0.1)
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("level", ["simple", "normal", "complex"])
def test_safe_fallback_respects_length_band_and_all_validators(level: str) -> None:
    move = professional_review()
    complexity = ProfessionalComplexity(level=level, reasons=["固定测试"])
    analysis = build_safe_professional_analysis(move, complexity)
    length = _narrative_length(analysis.model_dump(by_alias=True))
    assert LENGTH_RANGES[level][0] <= length <= LENGTH_RANGES[level][1]
    assert validate_professional_analysis(analysis, build_validation_context(move, level)) == []


def test_complex_safe_fallback_compacts_variable_fact_text_without_losing_evidence() -> None:
    move = professional_review()
    move.position_facts.piece_activity.extend(
        [
            EvidenceFact(
                id=f"fact:long-activity:{index}",
                category="verified_activity",
                side="white",
                description="白方棋子的活动事实已经由当前局面验证，分析只能引用这条事实。" * 8,
                evidence=["固定复杂局面回归测试"],
                squares=["b1"],
            )
            for index in range(2)
        ]
    )
    complexity = ProfessionalComplexity(level="complex", reasons=["可变事实文本较长"])
    analysis = build_safe_professional_analysis(move, complexity)
    length = _narrative_length(analysis.model_dump(by_alias=True))

    assert LENGTH_RANGES["complex"][0] <= length <= LENGTH_RANGES["complex"][1]
    assert validate_professional_analysis(
        analysis,
        build_validation_context(move, "complex"),
    ) == []
    assert "keyPieces" not in analysis.model_dump(by_alias=True)


def test_nested_extra_fields_are_rejected_in_strict_json() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    payload = build_safe_professional_analysis(move, complexity).model_dump(by_alias=True)
    payload["positionAssessment"]["kingSafety"]["unexpected"] = "hallucinated"
    parsed, errors = parse_professional_analysis(json.dumps(payload, ensure_ascii=False))
    assert parsed is None
    assert errors


def test_fact_squares_are_allowed_even_when_they_are_empty_in_starting_fen() -> None:
    move = professional_review()
    fact = EvidenceFact(
        id="fact:test:empty-target:h3",
        category="verified_target",
        side="white",
        description="结构化事实明确涉及h3格",
        evidence=["固定事实"],
        squares=["h3"],
    )
    move.position_facts.piece_activity.append(fact)
    move.allowed_squares = [square for square in move.allowed_squares if square != "h3"]
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    assert "h3" in context.allowed_squares
    analysis = build_safe_professional_analysis(move, complexity)
    analysis.position_assessment.piece_activity = ProfessionalEvidenceText(
        description="结构化事实涉及h3格。",
        evidenceRefs=[fact.id],
    )
    errors = validate_professional_analysis(analysis, context, enforce_length=False)
    assert not any("事实包之外的格子" in error for error in errors)


def test_validator_rejects_wrong_side_and_cross_route_evidence() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    analysis = build_safe_professional_analysis(move, complexity)
    black_king_ref = next(
        fact.id for fact in move.position_facts.king_safety if fact.side == "black"
    )
    analysis.position_assessment.king_safety.is_relevant = True
    analysis.position_assessment.king_safety.white = ProfessionalEvidenceText(
        description="白王安全结论引用了错误颜色证据。",
        evidenceRefs=[black_king_ref],
    )
    analysis.candidate_lines[0].evidence_refs = [move.candidate_lines[1].id]
    analysis.candidate_lines[0].continuation_phases[0].evidence_refs = [
        move.candidate_lines[1].moves[0].id
    ]
    errors = validate_professional_analysis(analysis, context, enforce_length=False)
    assert any("white王安全" in error for error in errors)
    assert any("候选路线1没有引用自身路线证据" in error for error in errors)
    assert any("候选路线1的阶段没有引用自身PV证据" in error for error in errors)


def test_complexity_thresholds_produce_simple_normal_and_complex() -> None:
    simple = professional_review()
    simple.candidate_lines = simple.candidate_lines[:1]
    simple.position_facts.immediate_checks = []
    simple.position_facts.immediate_captures = []
    simple.position_facts.threats = []
    simple.position_facts.piece_activity = []
    simple.position_facts.pawn_structure = []
    simple.complexity_factors.evaluation_swing_cp = 0
    simple.complexity_factors.only_reasonable_move = False
    assert compute_professional_complexity(simple).level == "simple"

    normal = professional_review()
    assert compute_professional_complexity(normal).level == "normal"

    complex_move = simple.model_copy(deep=True)
    complex_move.candidate_lines[0].mate_in = 2
    complex_move.complexity_factors.evaluation_swing_cp = 250
    complex_move.complexity_factors.only_reasonable_move = True
    assert compute_professional_complexity(complex_move).level == "complex"


@pytest.mark.parametrize(
    ("fact_id", "side", "square", "description"),
    [
        ("fact:test:undefended:g5", "white", "g5", "白马g5当前没有本方棋子保护"),
        ("fact:test:undefended:a7", "black", "a7", "黑兵a7当前没有本方棋子保护"),
        ("fact:test:undefended:h4", "white", "h4", "白象h4当前没有本方棋子保护"),
        ("fact:test:undefended:b7", "black", "b7", "黑象b7当前没有本方棋子保护"),
    ],
)
def test_undefended_piece_without_concrete_exploitation_is_not_a_weakness(
    fact_id: str,
    side: str,
    square: str,
    description: str,
) -> None:
    move = professional_review()
    move.position_facts.piece_activity.append(EvidenceFact(
        id=fact_id,
        category="undefended_piece",
        side=side,
        description=description,
        evidence=["固定回归事实"],
        squares=[square],
    ))
    focus = select_analysis_focus(move)
    selected = [item for values in focus.weaknesses.values() for item in values]
    assert fact_id not in {item.id for item in selected}
    rejected = next(item for item in focus.facts if item.id == fact_id)
    assert rejected.importance_score == 0
    assert "仅仅没有保护" in (rejected.rejection_reason or "")


def test_castling_rights_and_pawn_shield_alone_do_not_trigger_king_safety() -> None:
    move = professional_review()
    move.position_facts.king_safety = [
        EvidenceFact(
            id="fact:test:king:g1",
            category="king_square",
            side="white",
            description="白王位于g1",
            evidence=["固定回归事实"],
            squares=["g1"],
        ),
        EvidenceFact(
            id="fact:test:castle:g1",
            category="castling_rights",
            side="white",
            description="白方没有易位权",
            evidence=["固定回归事实"],
            squares=["g1"],
        ),
        EvidenceFact(
            id="fact:test:shield:g8",
            category="pawn_shield",
            side="black",
            description="g8王前相邻一排有1枚本方兵",
            evidence=["固定回归事实"],
            squares=["g8", "g7"],
        ),
    ]
    focus = select_analysis_focus(move)
    assert focus.king_safety_relevant_sides == frozenset()
    safe = build_safe_professional_analysis(move, compute_professional_complexity(move))
    assert safe.position_assessment.king_safety.is_relevant is False
    assert safe.position_assessment.king_safety.white is None
    assert safe.position_assessment.king_safety.black is None


def test_continuous_forcing_checks_trigger_only_supported_king_safety() -> None:
    move = professional_review()
    move.candidate_lines[0].moves[0].check = True
    move.candidate_lines[0].moves[2].check = True
    focus = select_analysis_focus(move)
    assert "black" in focus.king_safety_relevant_sides
    assert "white" not in focus.king_safety_relevant_sides


def test_single_line_gxh4_stays_inside_candidate_line_one() -> None:
    move = professional_review()
    event_move = move.candidate_lines[0].moves[3]
    event_move.san = "gxh4"
    event_move.from_square = "g5"
    event_move.to_square = "h4"
    event_move.capture = True
    event_move.captured_piece = "white_bishop"
    move.position_facts.threats.append(EvidenceFact(
        id="fact:test:route:gxh4",
        category="direct_piece_loss",
        side="white",
        description="候选路线1第4个半回合gxh4包含吃子",
        evidence=["固定回归路线"],
        squares=["g5", "h4"],
    ))
    focus = select_analysis_focus(move)
    assert not focus.global_threats
    assert {item.id for item in focus.line_events[1]} == {"fact:test:route:gxh4"}
    assert not focus.line_events[2]
    assert not focus.line_events[3]


def test_ordinary_pv_capture_is_not_promoted_to_global_threat() -> None:
    move = professional_review()
    move.position_facts.threats.append(EvidenceFact(
        id="fact:test:ordinary-pv-capture",
        category="route_event",
        side="white",
        description="候选路线1第3个半回合Nxd4包含吃子",
        evidence=["固定回归路线"],
        squares=["f3", "d4"],
    ))
    focus = select_analysis_focus(move)
    assert "fact:test:ordinary-pv-capture" not in {item.id for item in focus.global_threats}
    assert "fact:test:ordinary-pv-capture" not in {item.id for item in focus.line_events[1]}


def test_safe_analysis_does_not_bypass_threat_package_for_legal_check() -> None:
    move = professional_review()
    move.position_facts.immediate_checks.append(MoveFacts(
        id="fact:test:unconfirmed-check",
        san="Qh5+",
        uci="d1h5",
        from_square="d1",
        to_square="h5",
        piece="white_queen",
        capture=False,
        captured_piece=None,
        check=True,
        checkmate=False,
        castling=False,
        promotion=None,
    ))

    focus = select_analysis_focus(move)
    assert "fact:test:unconfirmed-check" in {item.id for item in focus.global_threats}

    safe = build_safe_professional_analysis(move, compute_professional_complexity(move))
    assert safe.main_danger.side_in_danger == "none"
    assert "直接危险来自" not in safe.main_danger.description
    assert safe.threats == []


def test_quiet_position_can_return_empty_weaknesses_and_threats() -> None:
    move = professional_review()
    focus = select_analysis_focus(move)
    assert focus.weaknesses == {"white": (), "black": ()}
    assert focus.global_threats == ()


def test_material_remains_raw_but_fixed_material_section_is_removed() -> None:
    move = professional_review()
    assert move.position_facts.material.get("id")
    safe = build_safe_professional_analysis(move, compute_professional_complexity(move))
    response_payload = safe.model_dump(by_alias=True, exclude_none=True)
    assert "material" not in response_payload["positionAssessment"]


def test_direct_piece_loss_is_allowed_only_as_route_consequence() -> None:
    move = professional_review()
    event_move = move.candidate_lines[1].moves[1]
    event_move.san = "Qxd4"
    event_move.from_square = "d8"
    event_move.to_square = "d4"
    event_move.capture = True
    event_move.captured_piece = "white_rook"
    move.position_facts.threats.append(EvidenceFact(
        id="fact:test:route:major-loss",
        category="direct_piece_loss",
        side="white",
        description="候选路线2中的Qxd4直接吃掉价值至少3分的棋子",
        evidence=["固定回归路线"],
        squares=["d8", "d4"],
    ))
    safe = build_safe_professional_analysis(move, compute_professional_complexity(move))
    assert not safe.threats
    assert [event.scope for event in safe.candidate_lines[1].events] == ["candidate_line_2"]
    assert not safe.candidate_lines[0].events
    assert not safe.candidate_lines[2].events


def test_hard_fact_guard_replaces_material_castling_and_best_move_claims() -> None:
    move = professional_review()
    move.before_fen = (
        "rnbq1rk1/pppp1ppp/5n2/4p3/4P3/5N2/"
        "PPPP1PPP/RNBQK2R w KQ - 4 4"
    )
    move.best_move_uci = "d2d4"
    move.best_move_san = "d4"
    analysis = build_safe_professional_analysis(
        move,
        compute_professional_complexity(move),
    )
    analysis.position_assessment.summary = "白方多一兵，黑方准备易位。"
    analysis.played_move_analysis.evaluation_reason = "实战着与引擎首选一致。"

    guarded = apply_hard_fact_guard(analysis, move)

    assert "双方物质相等" in guarded.position_assessment.summary
    assert "黑方王位于g8" in guarded.position_assessment.summary
    assert "仅凭当前局面不能判断此前是否已经易位" in guarded.position_assessment.summary
    assert "准备易位" not in guarded.position_assessment.summary
    assert "与Stockfish首选不一致" in guarded.played_move_analysis.evaluation_reason


def test_hard_fact_guard_injects_program_verified_tactical_context() -> None:
    move = professional_review()
    move.verified_tactics = [
        VerifiedTactic(
            name="double_attack",
            side="white",
            move_uci=move.played_move.uci,
            description="e4后该兵同时攻击两个目标。",
            squares=["e4", "d5", "f5"],
        ),
        VerifiedTactic(
            name="pin",
            side="black",
            move_uci=move.actual_move_line.moves[0].uci,
            description="e5后同时攻击白王（e1）和白马（g1）。",
            squares=["e5", "e1"],
        ),
    ]
    analysis = build_safe_professional_analysis(
        move,
        compute_professional_complexity(move),
    )

    guarded = apply_hard_fact_guard(analysis, move)

    assert guarded.played_move_analysis.intention == "e4后该兵同时攻击两个目标。"
    assert "e4后该兵同时攻击两个目标。" in guarded.played_move_analysis.positive_effects
    assert any("e5后同时攻击白王和白马" in item for item in guarded.played_move_analysis.problems)
    assert all("白王（e1）" not in item for item in guarded.played_move_analysis.problems)
    assert all("白马（g1）" not in item for item in guarded.played_move_analysis.problems)

    context = build_validation_context(move, compute_professional_complexity(move).level)
    assert "DeepSeek自由文本重写了程序控制的硬事实" not in validate_professional_analysis(guarded, context)


def test_hard_fact_guard_surfaces_two_rooks_on_seventh_rank() -> None:
    move = professional_review()
    move.position_facts.pieces.extend([
        {"id": "piece:test:white-rook-c7", "side": "white", "piece": "rook", "square": "c7"},
        {"id": "piece:test:white-rook-d7", "side": "white", "piece": "rook", "square": "d7"},
    ])
    analysis = build_safe_professional_analysis(
        move,
        compute_professional_complexity(move),
    )

    guarded = apply_hard_fact_guard(analysis, move)

    assert guarded.position_assessment.piece_activity is not None
    assert "两辆车已经位于第七横线" in guarded.position_assessment.piece_activity.description


def test_safe_professional_analysis_surfaces_program_confirmed_prepared_threat() -> None:
    move = professional_review()
    route_id = move.candidate_lines[0].id
    threat = ThreatFact(
        threat_id="prepared_threat_test",
        type="prepared_tactic",
        scope="prepared_threat",
        side="white",
        target="d5、f5",
        supporting_moves=["e4"],
        preparation_moves=["e4"],
        evidence_route_ids=[route_id],
        evidence=["python-chess确认e4形成双攻"],
        ignore_test=ThreatIgnoreTest(
            performed=True,
            ignored_move="a6、h6",
            evaluation_before=0.2,
            evaluation_after=2.1,
            evaluation_loss=1.9,
        ),
        confidence="high",
        source="python-chess+stockfish+ignore-test",
    )
    package = ThreatPackage(
        position_id=position_id(move.before_fen),
        threats=[threat],
        prepared_threats=[threat],
    )

    safe = build_safe_professional_analysis(
        move,
        compute_professional_complexity(move),
        threat_package=package,
    )

    assert any("准备型威胁" in item.description for item in safe.threats)
    assert any("Ignore Test" in item.preparation for item in safe.threats)


def test_professional_draft_accepts_context_approved_threat_id() -> None:
    move = professional_review()
    threat = ThreatFact(
        threat_id="prepared_threat_test",
        type="prepared_tactic",
        scope="prepared_threat",
        side="white",
        target="d5、f5",
        supporting_moves=["e4"],
        preparation_moves=["e4"],
        evidence_route_ids=[move.candidate_lines[0].id],
        evidence=["python-chess确认e2e4形成双攻"],
        ignore_test=ThreatIgnoreTest(
            performed=True,
            ignored_move="a6、h6",
            evaluation_before=0.2,
            evaluation_after=2.1,
            evaluation_loss=1.9,
        ),
        confidence="high",
        source="python-chess+stockfish+ignore-test",
    )
    package = ThreatPackage(
        position_id=position_id(move.before_fen),
        threats=[threat],
        prepared_threats=[threat],
    )
    context = build_validation_context(
        move,
        "normal",
        threat_package=package,
    )
    payload = _valid_reference_draft(move).model_dump(by_alias=True)
    payload["mainDanger"]["evidenceRefs"] = [threat.threat_id]
    draft = ProfessionalAnalysisDraft.model_validate(payload)

    assert validate_professional_draft(draft, move, context) == []


def test_prepared_threat_uses_verified_route_for_concrete_danger_squares() -> None:
    move = professional_review()
    route = move.candidate_lines[0]
    preparation = route.moves[0]
    threat = ThreatFact(
        threat_id="prepared_threat_route_test",
        type="tactical_capture",
        scope="prepared_threat",
        side=preparation.side,
        target=route.moves[1].to_square,
        supporting_moves=[route.moves[1].san],
        preparation_moves=[preparation.san],
        evidence_route_ids=[route.id],
        evidence=["路线中的准备着通过Ignore Test"],
        ignore_test=ThreatIgnoreTest(
            performed=True,
            ignored_move="a6",
            evaluation_before=0.2,
            evaluation_after=2.1,
            evaluation_loss=1.9,
        ),
        confidence="high",
        source="python-chess+stockfish+ignore-test",
    )
    package = ThreatPackage(
        position_id=position_id(move.before_fen),
        threats=[threat],
        prepared_threats=[threat],
    )
    complexity = compute_professional_complexity(move)
    guarded = apply_hard_fact_guard(
        build_safe_professional_analysis(
            move,
            complexity,
            threat_package=package,
        ),
        move,
        threat_package=package,
    )
    context = build_validation_context(
        move,
        complexity.level,
        threat_package=package,
    )

    assert preparation.from_square in guarded.main_danger.description
    assert preparation.to_square in guarded.main_danger.description
    assert validate_professional_analysis(guarded, context) == []


def test_professional_validation_blocks_occupied_target_and_unknown_initiative() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    analysis = apply_hard_fact_guard(
        build_safe_professional_analysis(move, complexity),
        move,
    )
    analysis.main_danger.description = "白方为该棋子让出f1格，并掌握主动权。"
    context = build_validation_context(
        move,
        complexity.level,
        initiative_side="unknown",
    )

    errors = validate_professional_analysis(analysis, context)

    assert any("已有棋子" in error for error in errors)
    assert any("主动权证据门禁" in error for error in errors)


def test_program_owned_claim_normalizer_rebuilds_whole_sentences_and_keeps_validation_strict() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    analysis = apply_hard_fact_guard(
        build_safe_professional_analysis(move, complexity),
        move,
    )
    analysis.comparison.main_difference = (
        "第一条路线先处理中心张力。白方拥有明显优势。"
        "随后再根据对手回应调整部署。"
    )
    analysis.main_danger.description = "白方为该棋子让出f1格，并掌握主动权。"
    context = build_validation_context(
        move,
        complexity.level,
        initiative_side="unknown",
    )

    before_errors = validate_professional_analysis(
        analysis,
        context,
        enforce_length=False,
    )
    normalized, paths = normalize_program_owned_claims(analysis, context)
    after_errors = validate_professional_analysis(
        normalized,
        context,
        enforce_length=False,
    )

    assert "DeepSeek自由文本重写了程序控制的硬事实" in before_errors
    assert any("主动权证据门禁" in error for error in before_errors)
    assert normalized.comparison.main_difference == (
        "第一条路线先处理中心张力。随后再根据对手回应调整部署。"
    )
    assert normalized.main_danger.description == (
        "该项不作额外评价，具体结论以程序事实与已验证路线为准。"
    )
    assert "comparison.mainDifference" in paths
    assert "mainDanger.description" in paths
    assert not any("硬事实" in error or "主动权证据门禁" in error for error in after_errors)


def test_professional_prompt_exposes_program_owned_fact_and_initiative_policy() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    payload = build_professional_payload(
        move,
        complexity,
        context.allowed_evidence_ids,
    )
    prompt = professional_user_prompt(payload, complexity.level)

    assert payload["interpretationPolicy"]["initiative"]["side"] == "unknown"
    assert payload["interpretationPolicy"]["hardFacts"] == "program_controlled"
    assert "Stockfish分数不能直接推出主动权" in professional_system_prompt()
    assert "物质差、王位置、易位、评价方向、走法质量" in prompt
