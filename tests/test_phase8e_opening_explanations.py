from __future__ import annotations

from scripts.build_phase8e_opening_explanations import (
    build_payload,
    clean_extract,
    clean_wikitext,
    parse_title_path,
)


def test_wikibooks_title_is_rebuilt_as_legal_move_path() -> None:
    result = parse_title_path(
        "Chess Opening Theory/1. e4/1...c5/2. Nf3/2...d6/3. d4"
    )

    assert result is not None
    san, uci = result
    assert san == ["e4", "c5", "Nf3", "d6", "d4"]
    assert uci == ["e2e4", "c7c5", "g1f3", "d7d6", "d2d4"]


def test_extract_removes_reference_sections_and_preserves_whole_prose() -> None:
    text = "Overview\nWhite fights for the centre with rapid development.\nReferences\nBook"

    cleaned = clean_extract(text)

    assert "White fights" in cleaned
    assert "References" not in cleaned
    assert "Book" not in cleaned


def test_wikitext_cleanup_removes_templates_and_preserves_link_labels() -> None:
    text = "{{BookCat}} The [[Sicilian Defence|Sicilian]] creates imbalance бд quickly. <ref>source</ref>"

    cleaned = clean_wikitext(text)

    assert cleaned == "The Sicilian creates imbalance · quickly."


def test_coverage_uses_longest_available_explanation_prefix() -> None:
    explanations = [{"uciMoves": ["e2e4", "c7c5"]}]
    catalog = {"openings": [{
        "uciMoves": ["e2e4", "c7c5", "g1f3"], "familyName": "Sicilian Defense"
    }]}

    payload = build_payload(explanations, catalog)

    assert payload["summary"]["coveredOpenings"] == 1
    assert payload["summary"]["coveredFamilies"] == 1
