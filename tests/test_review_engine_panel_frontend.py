from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _pages() -> list[str]:
    return [
        (ROOT / "index.html").read_text(encoding="utf-8"),
        (ROOT / "docs" / "index.html").read_text(encoding="utf-8"),
    ]


def test_review_engine_ui_is_hidden_until_game_review_exists() -> None:
    for page in _pages():
        assert 'id="reviewEvalRail" aria-label="当前局面评价" hidden' in page
        assert 'id="reviewEnginePanel" aria-live="polite" hidden' in page
        assert 'stage.classList.toggle("review-active", active);' in page
        assert "rail.hidden = !active;" in page
        assert "panel.hidden = !active;" in page


def test_review_evaluation_rail_sits_to_the_right_of_the_board() -> None:
    for page in _pages():
        board = page.index('<div class="board-shell" id="visualBoard"></div>')
        rail = page.index('<aside class="review-eval-rail" id="reviewEvalRail"')
        assert board < rail
        assert "grid-template-columns:minmax(0,1fr) 32px" in page
        assert 'class="review-eval-side black"' not in page
        assert 'class="review-eval-side white"' not in page
        assert "已完成分析 · 跟随复盘更新" not in page


def test_review_engine_ui_follows_current_review_position() -> None:
    for page in _pages():
        assert "function reviewPositionEngineData()" in page
        assert "evaluation: reviews[currentStep].before" in page
        assert "lines: reviews[currentStep].candidate_lines || []" in page
        assert "evaluation: lastReview.after" in page
        assert "lastReview.actual_move_line ? [lastReview.actual_move_line] : []" in page
        assert "renderReviewEnginePanel();" in page


def test_development_and_release_pages_remain_synchronized() -> None:
    assert (ROOT / "index.html").read_bytes() == (ROOT / "docs" / "index.html").read_bytes()
