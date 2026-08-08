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
        assert ".review-explanation[hidden] { display:none; }" in page
        assert "潜在威胁" not in page
        assert "双方计划" in page
        assert "王的安全" not in page
        assert "值得关注的弱点" not in page
        assert "其他优选招法" not in page
        assert "const lines = analysis.candidateLines || [];" in page
        assert "const routeCards = lines.map(line =>" in page
        assert "直接目的：" not in page
        assert "第一步作用：" in page
        assert "line.directPurpose" in page
        assert "line.events" not in page
        assert "professional-route-event" not in page
        assert "const needsMoveExplanation = Boolean(activeReview" in page
        assert 'String(activeReview.quality_symbol || "").includes("?")' in page
        assert "问题在哪里</p>" not in page
        assert "导致的结果" not in page
        assert "验证路线" in page
        assert "最终战术结果" in page
        assert "played.continuationPhases" in page


def test_castling_history_sentences_are_removed_from_position_display() -> None:
    for page in _pages():
        assert "function professionalPositionSentences(value)" in page
        assert '!item.includes("易位")' in page
        assert "].flatMap(professionalPositionSentences)" in page


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


def test_book_reference_stays_in_backend_payload_but_is_not_rendered() -> None:
    for page in _pages():
        assert "const bookReferences = payload.bookReferences || [];" not in page
        assert "棋书原评（精确局面）" not in page
        assert "bookReferences.forEach(reference =>" not in page
        assert 'if (!payload.analysis) throw new Error' in page
