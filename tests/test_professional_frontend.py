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


def test_professional_body_is_not_visually_truncated_and_routes_are_readable() -> None:
    for page in _pages():
        assert "white-space:normal; overflow:visible; overflow-wrap:anywhere" in page
        assert "height:auto; max-height:none; text-overflow:clip" in page
        assert ".professional-route-head { display:flex; flex-wrap:wrap" in page
        assert ".professional-route-title { white-space:nowrap; font-size:clamp(22px,2.2vw,28px)" in page
        assert ".professional-tag { padding:7px 11px" in page
        assert ".professional-section p,.professional-list li,.professional-label,.professional-route-eval { font-size:16px; }" in page


def test_professional_report_hides_judgement_process_and_empty_danger() -> None:
    for page in _pages():
        assert '.replace(/依据.*?(?:可以判断|可判断)/g, "")' in page
        assert '.replace(/根据.*?(?:可以判断|可判断)/g, "")' in page
        assert "const noConcreteDanger =" in page
        assert "&& !noConcreteDanger &&" in page
