"""Inline above-the-fold image assets to avoid slow per-request Pages latency."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "docs" / "assets"
HTML_FILES = (ROOT / "index.html", ROOT / "docs" / "index.html")


def data_uri(path: Path, *, max_size: tuple[int, int], quality: int) -> str:
    image = Image.open(path)
    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=quality, method=6)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{encoded}"


def main() -> None:
    replacements = {
        'assets/chess/physical_chess_board_empty.webp': data_uri(
            ASSET_ROOT / "chess" / "physical_chess_board_empty.webp",
            max_size=(1024, 1024),
            quality=68,
        ),
        'assets/mascot-pawn-detective-v2.webp': data_uri(
            ASSET_ROOT / "mascot-pawn-detective-v2.webp",
            max_size=(512, 768),
            quality=74,
        ),
    }
    pieces = ASSET_ROOT / "chess" / "pieces_transparent"
    for color, prefix, names in (("P", "white", {
            "P": "pawn_a2",
            "N": "knight_b1",
            "B": "bishop_c1",
            "R": "rook_a1",
            "Q": "queen_d1",
            "K": "king_e1",
        }), ("p", "black", {
            "P": "pawn_a7",
            "N": "knight_b8",
            "B": "bishop_c8",
            "R": "rook_a8",
            "Q": "queen_d8",
            "K": "king_e8",
        })):
        for piece, name in names.items():
            key = piece if color == "P" else piece.lower()
            filename = f"{prefix}_{name}.webp"
            replacements[f"assets/chess/pieces_transparent/{filename}"] = data_uri(
                pieces / filename,
                max_size=(144, 144),
                quality=76,
            )

    contents = [path.read_text(encoding="utf-8") for path in HTML_FILES]
    for index, content in enumerate(contents):
        for old, new in replacements.items():
            content = content.replace(old, new)
        HTML_FILES[index].write_text(content, encoding="utf-8", newline="\n")
    if HTML_FILES[0].read_bytes() != HTML_FILES[1].read_bytes():
        raise RuntimeError("index.html and docs/index.html diverged")
    print(f"inlined {len(replacements)} critical assets into {len(HTML_FILES)} HTML files")


if __name__ == "__main__":
    main()
