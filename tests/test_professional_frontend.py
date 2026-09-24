from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _pages() -> list[str]:
    return [
        (ROOT / "index.html").read_text(encoding="utf-8"),
        (ROOT / "docs" / "index.html").read_text(encoding="utf-8"),
    ]


def test_development_and_published_pages_are_synchronized() -> None:
    development, published = _pages()
    assert development == published


def test_program_verified_played_best_comparison_is_rendered() -> None:
    for page in _pages():
        assert "activeReview?.playedBestComparison" in page
        assert "你的走法 vs 更好的走法" in page
        assert "真正的区别" in page
        assert "programmedComparison.mainDifference" in page


def test_professional_analysis_is_unified_and_expanded_by_default() -> None:
    for page in _pages():
        assert page.count('id="professionalAnalysisPanel"') == 1
        assert page.count("小兵研究员说") == 1
        assert "professionalAnalysisBtn" not in page
        assert "展开详细分析" not in page
        assert "收起专业分析" not in page
        assert "AI详细说明暂时没有生成" not in page
        assert "记住：先核对棋盘事实" not in page
        assert "loadProfessionalAnalysis(review);" in page


def test_every_selected_move_has_race_safe_explanation_and_verified_fallback() -> None:
    for page in _pages():
        assert "const professionalAnalysisPending = new Map();" in page
        assert "const token = ++professionalRequestToken;" in page
        assert "const key = `${analysisId}:${review.index}:${openingRevision}`;" in page
        assert "await new Promise(resolve => setTimeout(resolve, 250));" in page
        assert "professionalAnalysisPending.get(key)" in page
        assert "renderVerifiedProfessionalFallback(review);" in page
        assert "重新生成详细解释" in page


def test_professional_conclusions_are_not_visually_truncated() -> None:
    for page in _pages():
        assert "white-space:normal; overflow:visible; overflow-wrap:anywhere" in page
        assert "height:auto; max-height:none; text-overflow:clip" in page
        assert ".professional-section p,.professional-list li,.professional-label,.professional-route-eval { font-size:16px; }" in page


def test_professional_report_only_renders_conclusion_sections() -> None:
    for page in _pages():
        assert '.replace(/依据.*?(?:可以判断|可判断)/g, "")' in page
        assert '.replace(/根据.*?(?:可以判断|可判断)/g, "")' in page
        assert "最大危险" not in page
        assert "如果不处理：" not in page
        assert "可能后果：" not in page
        assert "实战走法分析" not in page
        assert "Stockfish 三条研究路线" in page
        assert "主要变化：" not in page
        assert "原始分析数据" not in page
        assert 'panel.hidden = !parts.length;' not in page
        assert 'panel.hidden = false;' in page
        assert "当前没有需要特别强调的结论。" not in page
        assert "const positionParagraphs = [" in page
        assert "双方子力与局面" in page
        assert "const opening = payload.openingContext || activeReview?.openingContext || gameReview?.openingSummary || null;" in page
        assert "opening?.displayName" in page
        assert "开局名称：" in page
        assert "所处分支：" in page
        assert "开局说明" in page
        assert "白方思路" in page
        assert "黑方思路" in page
        assert "opening?.variationNameZh" in page
        assert "professional-opening-context" in page
        assert "professional-opening-identity" in page
        assert "professional-opening-notes" in page
        assert '["开局说明", openingDescription]' in page
        assert '["白方思路", openingWhitePlan]' in page
        assert '["黑方思路", openingBlackPlan]' in page
        assert '.replace(/\\bWhite\\b/gi, "白方").replace(/\\bBlack\\b/gi, "黑方")' in page
        assert '.replace(/怀特/g, "白方").replace(/布莱克/g, "黑方")' in page
        assert "const openingParagraphs = value =>" in page
        assert '.split(/\\n\\s*\\n+/)' in page
        assert '.split(/\\s+(?=(?:\\d+)' not in page
        assert 'class="professional-opening-branch"' in page
        assert "text-indent:2em" in page
        assert "const OPENING_CONTEXT_UI_VERSION = 8;" in page
        assert '.replace(/主教/g, "象")' in page
        assert '.replace(/骑士/g, "马")' in page
        assert "const normalizeOpeningChessTerms = value =>" in page
        assert "const openingTextLooksNatural = value =>" in page
        assert '`${analysisId}:${review.index}:${openingRevision}`' in page
        assert "if (cached?._openingContextVersion === OPENING_CONTEXT_UI_VERSION)" in page
        assert "payload._openingContextVersion = OPENING_CONTEXT_UI_VERSION;" in page
        assert 'italian: "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d4 exd4' in page
        assert ".review-explanation[hidden] { display:none; }" in page
        assert "潜在威胁" not in page
        assert "双方计划" in page
        assert "王的安全" not in page
        assert "值得关注的弱点" not in page
        assert "其他优选招法" not in page
        assert "const lines = analysis.candidateLines || [];" in page
        assert "const routeCards = lines.map(line =>" in page
        assert "直接目的：" not in page
        assert "第一步作用：" not in page
        assert "line.directPurpose" not in page
        assert "line.opponentResponse" not in page
        assert "line.advantages" not in page
        assert "line.risks" not in page
        assert '<div class="professional-moves">${escapeHtml(moves)}</div></div>' in page
        assert "line.events" not in page
        assert "professional-route-event" not in page
        assert "const showMoveAnalysis = Boolean(activeReview);" in page
        assert "if (showMoveAnalysis)" in page
        assert "分析结论" in page
        assert "const coachPoints = [played.intention || coachVerdict]" in page
        assert "问题在哪里</p>" not in page
        assert "导致的结果" not in page
        assert "验证路线" in page
        assert "最终战术结果" not in page
        assert "路线验证到的结果" in page
        assert "played.continuationPhases" in page


def test_move_conclusion_prefers_verified_core_over_generic_verdict() -> None:
    for page in _pages():
        assert "const coachPoints = [played.intention || coachVerdict]" in page


def test_verified_core_commentary_is_visible_in_the_professional_panel() -> None:
    for page in _pages():
        assert "const coreNarrative = completeProfessionalSentence(played.intention);" in page
        assert 'if (coreNarrative) {' in page
        assert "♟ 局面解读" in page
        assert "professional-core-narrative" in page


def test_research_routes_only_render_move_evaluation_and_variation() -> None:
    for page in _pages():
        assert "第一步作用：" not in page
        assert "line.directPurpose" not in page
        assert "line.opponentResponse" not in page
        assert "line.advantages" not in page
        assert "line.risks" not in page
        assert '<div class="professional-moves">${escapeHtml(moves)}</div></div>' in page


def test_castling_history_sentences_are_removed_from_position_display() -> None:
    for page in _pages():
        assert "function professionalPositionSentences(value)" in page
        assert '!item.includes("易位")' in page
        assert "].flatMap(professionalPositionSentences)" in page


def test_browser_scan_progress_stays_visible_after_engine_ready() -> None:
    for page in _pages():
        initialization = page.split("function initEngine()", 1)[1].split("function sendToEngine", 1)[0]
        assert 'document.getElementById("loading").style.display = "none"' not in initialization
        assert "本机引擎正在扫描 ${index + 1}/${moves.length} 个局面" in page


def test_key_pieces_feature_is_absent_from_both_pages() -> None:
    for page in _pages():
        assert "关键棋子" not in page
        assert "keyPieces" not in page
        assert "keyPieceInsight" not in page


def test_current_position_analysis_card_is_absent_from_both_pages() -> None:
    for page in _pages():
        assert "当前局面分析" not in page
        assert "professional-position-analysis" not in page
        assert "currentParagraphs" not in page


def test_legacy_position_analysis_frontend_is_removed_and_move_review_remains() -> None:
    for page in _pages():
        assert 'id="analyzeBtn"' not in page
        assert 'onclick="analyze()"' not in page
        assert "只分析当前局面" not in page
        assert 'id="resultSection"' not in page
        assert "async function evaluateCurrentPosition()" not in page
        assert 'fetch(apiUrl("/api/review")' not in page
        assert 'id="moveReviewCard"' in page
        assert 'fetchWithTimeout(apiUrl("/api/game-review/start-local")' in page
        assert 'fetchWithTimeout(apiUrl("/api/game-review/position")' in page
        assert "scanGameInBrowser" in page
        assert "_authoritative" in page
        assert 'const scoreDirection = startFen.split(" ")[1] === "b" ? -1 : 1;' in page
        assert ".sort((a, b) => a - b)" in page
        assert "本机引擎不可用，正在切换后台完整分析" in page
        assert "小兵研究员说" in page


def test_core_coaching_explanation_is_rendered_as_one_ordered_paragraph() -> None:
    for page in _pages():
        assert "const coachPoints = [played.intention || coachVerdict]" in page
        assert '<p class="professional-label">分析结论</p><p>${coachPoints.map(escapeHtml).join("")}</p>' in page
        assert 'professional-coach-section"><h4>先说结论' not in page
        assert "...coachProblems" not in page
        assert "comparison.whyFirstLineIsBest || comparison.mainDifference" not in page


def test_book_reference_stays_in_backend_payload_but_is_not_rendered() -> None:
    for page in _pages():
        assert "const bookReferences = payload.bookReferences || [];" not in page
        assert "棋书原评（精确局面）" not in page
        assert "bookReferences.forEach(reference =>" not in page
        assert 'if (!payload.analysis) throw new Error' in page
