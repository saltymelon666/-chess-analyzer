import asyncio
import json
from unittest.mock import AsyncMock

import chess
from fastapi.testclient import TestClient
import pytest

from app import api
from app.game_review import _move_facts, _variation_moves
from app.models import (
    CandidateLine,
    ComplexityFactors,
    EvaluationSnapshot,
    GeneratedProfessionalAnalysis,
    EvidenceFact,
    MoveReview,
    ProfessionalComplexity,
    ProfessionalAnalysisUsage,
    ProfessionalAnalysisDraft,
)
from app.position_facts import extract_position_facts
from app.professional_analysis import (
    ChatResult,
    PROFESSIONAL_PROMPT_VERSION,
    PROFESSIONAL_TOKEN_LIMITS,
    ProfessionalAnalysisService,
    build_professional_payload,
    build_safe_professional_analysis,
    compute_professional_complexity,
    professional_cache_key,
    professional_user_prompt,
)
from app.professional_validation import (
    LENGTH_RANGES,
    _narrative_length,
    build_validation_context,
    parse_professional_analysis,
    validate_professional_analysis,
)
from app.professional_refs import (
    build_reference_payload,
    normalize_professional_draft_literals,
    resolve_professional_draft,
    validate_professional_draft,
)


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


def test_validator_rejects_hallucinated_evidence_square_piece_and_route() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    payload = build_safe_professional_analysis(move, complexity).model_dump(by_alias=True)
    payload["mainDanger"]["description"] = "黑后在h9攻击白王，白方会立即受损。"
    payload["mainDanger"]["sideInDanger"] = "white"
    payload["mainDanger"]["evidenceRefs"] = ["fact:not-real"]
    payload["keyPieces"][0].update({"side": "black", "piece": "queen", "square": "a1"})
    payload["playedMoveAnalysis"]["move"] = "Qh5"
    payload["playedMoveAnalysis"]["positiveEffects"] = ["实战走法吃掉了黑后。"]
    payload["candidateLines"][0]["firstMove"] = "Qh5"
    payload["candidateLines"][0]["continuationPhases"][0]["moves"] = ["d4"]
    from app.models import ProfessionalAnalysis
    analysis = ProfessionalAnalysis.model_validate(payload)
    errors = validate_professional_analysis(analysis, context, enforce_length=False)
    assert any("evidenceRefs" in error for error in errors)
    assert any("棋盘范围外" in error for error in errors)
    assert any("关键棋子" in error for error in errors)
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

        async def analyze(self, selected):
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
    assert '"v":"professional-v5-refs"' in prompt
    assert '"playedMoveAnalysis"' in prompt
    assert '"candidateLines"' in prompt
    assert '"pieceRef"' in prompt
    assert '"lineRef"' in prompt
    assert '"plyRefs"' in prompt
    assert '"positionAfter"' not in prompt
    assert '"allowedEvidenceIds"' not in prompt
    assert '"allowedMoves"' not in prompt
    assert '"uci"' not in prompt
    assert "$defs" not in prompt


def _valid_reference_draft(move: MoveReview) -> ProfessionalAnalysisDraft:
    payload = build_reference_payload(move, "normal", ["fixed test"])
    pieces = payload["pos"]["pieces"]
    facts = payload["pos"]["facts"]
    routes = payload["lines"]
    actual = payload["actual"]["plies"]

    material_ref = next(item["id"] for item in facts if item["kind"] == "material")

    def side_fact(side: str) -> str:
        return next(
            (
                item["id"]
                for item in facts
                if item.get("side") in {side, "neutral", None}
            ),
            material_ref,
        )

    def side_piece(side: str) -> dict:
        return next(item for item in pieces if item["side"] == side and item["piece"] != "pawn")

    def side_ply(side: str) -> str:
        return next(
            ply["id"]
            for route in routes
            for ply in route["plies"]
            if ply["side"] == side
        )

    danger_ply = routes[0]["plies"][0]
    white_piece = side_piece("white")
    black_piece = side_piece("black")

    return ProfessionalAnalysisDraft.model_validate(
        {
            "complexity": "normal",
            "positionAssessment": {
                "summary": "局面保持平衡，双方都需要先完成发展。",
                "material": {"explanation": "物质没有明显差距。", "evidenceRefs": [material_ref]},
                "kingSafety": {
                    "white": {"explanation": "白方需要继续保护王。", "evidenceRefs": [side_fact("white")]},
                    "black": {"explanation": "黑方也需要继续保护王。", "evidenceRefs": [side_fact("black")]},
                },
                "pieceActivity": {"explanation": "子力发展决定主动权。", "evidenceRefs": [material_ref]},
                "pawnStructure": {"explanation": "兵形暂时没有明显弱点。", "evidenceRefs": [material_ref]},
            },
            "mainDanger": {
                "level": "short_term",
                "dangerRef": danger_ply["id"],
                "explanation": "首要危险来自候选路线第一步造成的节奏变化。",
                "consequence": "若忽略这个变化，对手会取得主动。",
                "evidenceRefs": [danger_ply["id"]],
            },
            "keyPieces": {
                "white": {
                    "pieceRef": white_piece["id"],
                    "role": "负责协调白方子力并支持下一步计划。",
                    "futureTask": "它的活动会影响白方能否顺利发展。",
                    "evidenceRefs": [white_piece["id"]],
                },
                "black": {
                    "pieceRef": black_piece["id"],
                    "role": "负责协调黑方子力并限制对手计划。",
                    "futureTask": "它的部署会影响黑方的反击速度。",
                    "evidenceRefs": [black_piece["id"]],
                },
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
    assert resolved.key_pieces[0].square in move.pieces_before
    assert [item.rank for item in resolved.candidate_lines] == [1, 2, 3]
    assert resolved.candidate_lines[0].first_move == move.candidate_lines[0].first_move.san


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
    payload["keyPieces"]["white"]["pieceRef"] = "piece:not-real"
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

    assert any(issue.path == "keyPieces.white.pieceRef" and issue.category == "不存在的棋子" for issue in issues)
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
    assert all(item.evidence_refs for item in analysis.key_pieces)


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
    analysis.position_assessment.piece_activity.description += "；结构化事实涉及h3格。"
    analysis.position_assessment.piece_activity.evidence_refs.append(fact.id)
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
    analysis.position_assessment.king_safety.white.evidence_refs = [black_king_ref]
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
