from app.decision_context import build_decision_context, decision_history_signature
from tests.test_professional_analysis import professional_review


def _move(*, index: int, side: str, played: str, best: str, loss: int, quality: str):
    move = professional_review().model_copy(deep=True)
    move.index = index
    move.side = side
    move.played_move.uci = played
    move.best_move_uci = best
    move.centipawn_loss = loss
    move.quality_label = quality
    move.quality_key = "mistake" if loss >= 150 else "inaccuracy"
    return move


def test_decision_context_detects_repeated_non_best_for_same_side() -> None:
    prior_white = _move(
        index=1, side="white", played="e2e3", best="e2e4", loss=70, quality="不精确"
    )
    prior_black = _move(
        index=2, side="black", played="e7e5", best="e7e5", loss=0, quality="最佳着"
    )
    current = _move(
        index=3, side="white", played="d2d3", best="d2d4", loss=80, quality="不精确"
    )

    context = build_decision_context(
        current,
        [prior_white, prior_black],
        objective_kind="move_quality_explanation",
        objective_question="比较实战着与首选路线。",
    )

    assert context.trend == "repeated_non_best"
    assert "不由历史偏离推断当前机制" in context.core_pain_point
    assert len(context.recent_signals) == 2
    assert context.recent_signals[0].plies_ago == 2
    assert "主观意图" in context.boundary


def test_decision_context_first_move_uses_current_objective() -> None:
    current = _move(
        index=1, side="white", played="e2e4", best="e2e4", loss=0, quality="最佳着"
    )

    context = build_decision_context(
        current,
        [],
        objective_kind="forcing_tactics",
        objective_question="先解释当前强制手段和对手回应。",
    )

    assert context.trend == "first_decision"
    assert context.core_pain_point == "先解释当前强制手段和对手回应。"
    assert context.recent_signals == []
    assert "不制造两者差异" in context.must_answer[1]


def test_small_gap_history_does_not_force_a_punishment_story() -> None:
    prior = _move(
        index=1, side="white", played="e2e3", best="e2e4", loss=80, quality="不精确"
    )
    current = _move(
        index=3, side="white", played="d2d3", best="d2d4", loss=20, quality="好棋"
    )
    context = build_decision_context(
        current, [prior], objective_kind="move_quality_explanation",
        objective_question="比较实战着与首选路线。",
    )
    assert context.trend == "repeated_non_best"
    assert "评价差距很小" in context.core_pain_point
    assert "不强分高下" in context.must_answer[1]
    assert "没有优先解决" not in context.core_pain_point


def test_decision_history_signature_is_bounded_to_five_plies() -> None:
    moves = [
        _move(
            index=index,
            side="white" if index % 2 else "black",
            played="e2e3",
            best="e2e4",
            loss=20,
            quality="好棋",
        )
        for index in range(1, 8)
    ]

    signature = decision_history_signature(moves)

    assert len(signature) == 5
    assert signature[0]["side"] == moves[-5].side


def test_repeated_non_best_does_not_claim_uninterrupted_sequence() -> None:
    prior_non_best = _move(
        index=1, side="white", played="e2e3", best="e2e4", loss=70, quality="不精确"
    )
    prior_best = _move(
        index=3, side="white", played="g1f3", best="g1f3", loss=0, quality="最佳着"
    )
    current = _move(
        index=5, side="white", played="d2d3", best="d2d4", loss=70, quality="不精确"
    )

    context = build_decision_context(
        current,
        [prior_non_best, prior_best],
        objective_kind="move_quality_explanation",
        objective_question="比较实战着与首选路线。",
    )

    assert context.trend == "repeated_non_best"
    assert "近期多次偏离" in context.must_answer[-1]
    assert "连续偏离" not in context.must_answer[-1]
