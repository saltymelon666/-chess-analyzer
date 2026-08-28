from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _pages() -> list[str]:
    return [
        (ROOT / "index.html").read_text(encoding="utf-8"),
        (ROOT / "docs" / "index.html").read_text(encoding="utf-8"),
    ]


def test_board_uses_fixed_white_orientation_matching_pgn_coordinates() -> None:
    for page in _pages():
        assert "function boardCenter(row, col)" in page
        assert "const x = BOARD_META.margin + col * BOARD_META.square" in page
        assert "const y = BOARD_META.margin + row * BOARD_META.square" in page
        assert 'const flipped = turn === "b";' not in page
        assert "白方在下" in page


def test_mistake_positions_link_to_a_separate_local_practice_page() -> None:
    for page in _pages():
        assert 'id="reviewSaveRow" hidden' in page
        assert 'href="review.html"' in page
        assert 'id="reviewNavCount" hidden' in page
        assert 'id="reviewLibrary"' not in page
        assert 'id="reviewPractice"' not in page
        assert 'const REVIEW_STORAGE_KEY = "pawnlab_review_positions_v1"' in page
        assert 'const REVIEW_STORAGE_LIMIT = 50' in page
        assert 'new Set(["inaccuracy", "mistake", "blunder"])' in page
        assert "function toggleCurrentReviewPosition()" in page
        assert 'button.textContent = saved ? "开始复习" : "加入复习";' in page
        assert "window.location.href = `review.html?id=${encodeURIComponent(id)}`;" in page
        assert "review.before_fen" in page
        assert "review.best_move_uci" in page

    review_pages = [
        (ROOT / "review.html").read_text(encoding="utf-8"),
        (ROOT / "docs" / "review.html").read_text(encoding="utf-8"),
    ]
    assert review_pages[0] == review_pages[1]
    for page in review_pages:
        assert 'id="practiceView" hidden' in page
        assert 'id="visualBoard"' in page
        assert 'id="masteryActions" hidden' in page
        assert 'const REVIEW_STORAGE_KEY = "pawnlab_review_positions_v1"' in page
        assert "function handleSquare(square)" in page
        assert "game.undo(); practice.wrongSquares=[from,square];" in page
        assert "if (playedUci !== expected)" in page
        assert "practice.completed=true" in page
        assert 'id="promotionPicker" hidden' in page
        assert "practice.pendingPromotion={ from,to:square };" in page
        assert "attemptMove(practice.pendingPromotion.from,practice.pendingPromotion.to,promotion)" in page
        assert "button.tabIndex=practice?.focusSquare === square ? 0 : -1;" in page
        assert 'ArrowLeft:[0,-1]' in page
        assert 'button.setAttribute("aria-pressed",String(practice?.selectedSquare === square));' in page
        assert "function completePractice(level)" in page
        assert "const days={ again:1,learning:3,mastered:7 }[level] || 1;" in page
        assert 'orientation=game.turn() === "b" ? "black" : "white";' in page
        assert "答案只会在你走对之后显示" in page
        assert "function undoRemove()" in page
        assert "查看原局复盘说明" not in page
        assert "solutionExplanation" not in page


def test_board_renders_file_and_rank_coordinates() -> None:
    for page in _pages():
        assert "function renderBoardCoordinates(board)" in page
        assert "FILES.forEach((file, col)" in page
        assert "label.textContent = String(8 - row);" in page
        assert "renderBoardCoordinates(board);" in page
        assert ".board-coordinate {" in page


def test_public_beta_is_guest_accessible_and_tracks_core_events() -> None:
    for page in _pages():
        assert "公测体验版 · 免费使用" in page
        assert "无需注册" in page
        assert "Stockfish逐步评价 + DeepSeek中文报告" in page
        assert "正常体验不限制使用次数" in page
        assert "不收集姓名、邮箱等个人信息" in page
        assert 'id="previewNotice"' in page
        assert 'location.protocol === "file:"' in page
        assert "文件预览模式 · 请用 http://localhost:8080 打开以连接分析服务" in page
        assert 'id="pgnFileInput"' in page
        assert 'localStorage.getItem(VISITOR_STORAGE_KEY)' in page
        assert 'trackEvent("page_view"' in page
        assert 'trackEvent("upload_pgn"' in page
        assert 'trackEvent("analysis_start"' in page
        assert 'trackEvent("analysis_complete"' in page
        assert "pgnMoves.length > 200" in page
        assert 'id="loginForm"' not in page
        assert 'id="registerForm"' not in page
        assert 'href="/login"' not in page
        assert 'href="/register"' not in page
        assert "token限制" not in page
        assert "IP限制" not in page


def test_frontend_images_use_webp_and_chess_parser_is_lazy_loaded() -> None:
    for page in _pages():
        assert 'src="chess.js"' not in page
        assert 'import("./pgn-runtime.js")' in page
        assert "await ensureChessRuntime();" in page
        assert "flat_chess_board_v2.webp" in page
        assert "mascot-pawn-detective-v2.webp" in page
        assert 'loading="lazy"' in page
        assert 'fetchpriority="high"' in page

    assert (ROOT / "pgn-runtime.js").read_text(encoding="utf-8") == (
        ROOT / "docs" / "pgn-runtime.js"
    ).read_text(encoding="utf-8")
    assert not list((ROOT / "assets").rglob("*.png"))
    assert not list((ROOT / "docs" / "assets").rglob("*.png"))


def test_board_uses_centered_v3_piece_assets() -> None:
    for page in _pages():
        assert "flat_chess_board_v2.webp" in page
        assert "pieces-v3/white_pawn.webp" in page
        assert "pieces-v3/black_king.webp" in page
        assert "width: calc(100% * 190 / 2048)" in page
        assert "BOARD_META.square * 8 + 40" in page
        assert "BOARD_META.margin - 40" in page
        piece_mapping = page.split("const PIECE_ASSET =", 1)[1].split("const EXAMPLES", 1)[0]
        assert "data:image/webp;base64" not in piece_mapping

    for asset_root in (ROOT / "assets" / "chess", ROOT / "docs" / "assets" / "chess"):
        board_asset = asset_root / "flat_chess_board_v2.webp"
        assert board_asset.exists()
        assert board_asset.stat().st_size > 0

    piece_names = {
        f"{color}_{piece}.webp"
        for color in ("white", "black")
        for piece in ("pawn", "knight", "bishop", "rook", "queen", "king")
    }
    assert {path.name for path in (ROOT / "assets" / "chess" / "pieces-v3").iterdir()} == piece_names
    assert {path.name for path in (ROOT / "docs" / "assets" / "chess" / "pieces-v3").iterdir()} == piece_names


def test_admin_dashboard_is_separate_and_not_linked_from_product() -> None:
    product = (ROOT / "index.html").read_text(encoding="utf-8")
    admin = (ROOT / "admin.html").read_text(encoding="utf-8")
    published_admin = (ROOT / "docs" / "admin.html").read_text(encoding="utf-8")

    assert admin == published_admin
    assert "admin.html" not in product
    assert "PawnLab 公测运营后台" in admin
    assert "/api/admin/dashboard" in admin
    assert "X-Admin-Key" in admin
    assert "sessionStorage" in admin
    assert "总 Token" in admin
    assert "异常保护规则" in admin
    assert 'id="allTimeMetrics"' in admin
    assert "payload.historical_statistics" in admin
    assert 'id="dateInput"' not in admin
    assert "/api/admin/dashboard?limit=1000" in admin
    assert "全部历史统计" in admin
    assert "分析历史记录" in admin
    assert 'id="feedbackSummary"' in admin
    assert 'id="feedbackList"' in admin
    assert "评分与反馈" in admin
    assert "平均评分" in admin
    assert "文字反馈" in admin
    assert "查看对应分析结果" in admin


def test_feedback_submits_current_analysis_context() -> None:
    for page in _pages():
        assert "function currentFeedbackAnalysis()" in page
        assert "analysis_id: feedbackAnalysis.analysisId" in page
        assert "analysis_result: feedbackAnalysis.result" in page
        assert 'document.getElementById("professionalAnalysisContent")' in page
