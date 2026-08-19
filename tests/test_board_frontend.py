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


def test_board_renders_file_and_rank_coordinates() -> None:
    for page in _pages():
        assert "function renderBoardCoordinates(board)" in page
        assert "FILES.forEach((file, col)" in page
        assert "label.textContent = String(8 - row);" in page
        assert "renderBoardCoordinates(board);" in page
        assert ".board-coordinate {" in page


def test_homepage_feedback_form_is_available_in_both_frontends() -> None:
    for page in _pages():
        assert 'id="feedbackForm"' in page
        assert 'name="rating"' in page
        assert 'id="feedbackSuggestion"' in page
        assert 'maxlength="2000"' in page
        assert 'event: "feedback"' in page
        assert 'addEventListener("submit", submitFeedback)' in page


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
        assert "visitor_id: visitorId" in page
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
        assert "physical_chess_board_empty.webp" in page
        assert "mascot-pawn-detective-v2.webp" in page
        assert "pieces_transparent/white_pawn_a2.webp" in page
        assert 'loading="lazy"' in page
        assert 'fetchpriority="high"' in page

    assert (ROOT / "pgn-runtime.js").read_text(encoding="utf-8") == (
        ROOT / "docs" / "pgn-runtime.js"
    ).read_text(encoding="utf-8")
    assert not list((ROOT / "assets").rglob("*.png"))
    assert not list((ROOT / "docs" / "assets").rglob("*.png"))


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
