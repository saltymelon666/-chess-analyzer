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
