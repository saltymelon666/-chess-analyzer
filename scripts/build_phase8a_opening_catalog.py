from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
from typing import Any

import chess
import chess.pgn


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "work" / "research_books" / "lichess-chess-openings"
DEFAULT_OUTPUT = ROOT / "docs" / "research" / "phase8a-opening-path-catalog.json"
DEFAULT_MANIFEST = ROOT / "docs" / "research" / "phase8a-opening-path-manifest.json"
DEFAULT_RUNTIME_OUTPUT = ROOT / "app" / "data" / "opening-path-catalog.json"
SOURCE_URL = "https://github.com/lichess-org/chess-openings"


def _source_revision(source: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _position_key(board: chess.Board) -> str:
    ep = chess.square_name(board.ep_square) if board.ep_square is not None else "-"
    return " ".join((
        board.board_fen(),
        "w" if board.turn == chess.WHITE else "b",
        board.castling_xfen() or "-",
        ep,
    ))


def _parse_line(pgn: str) -> tuple[list[str], list[str], chess.Board]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("PGN line could not be parsed")
    board = game.board()
    san_moves: list[str] = []
    uci_moves: list[str] = []
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError(f"illegal move {move.uci()} in {pgn}")
        san_moves.append(board.san(move))
        uci_moves.append(move.uci())
        board.push(move)
    if not uci_moves:
        raise ValueError("opening line must contain at least one move")
    return san_moves, uci_moves, board


def _name_parts(name: str) -> tuple[str, list[str]]:
    family, separator, remainder = name.partition(":")
    variations = [part.strip() for part in remainder.split(",") if part.strip()] if separator else []
    return family.strip(), variations


def _opening_id(eco: str, name: str, uci_moves: list[str]) -> str:
    digest = hashlib.sha1(f"{eco}|{name}|{' '.join(uci_moves)}".encode("utf-8")).hexdigest()[:16]
    return f"OPENING-{digest}"


def load_rows(source: Path) -> list[dict[str, str]]:
    import csv

    rows: list[dict[str, str]] = []
    for volume in "abcde":
        path = source / f"{volume}.tsv"
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open(encoding="utf-8", newline="") as handle:
            rows.extend(dict(row) for row in csv.DictReader(handle, delimiter="\t"))
    return rows


def build_catalog(rows: list[dict[str, str]], *, revision: str) -> dict[str, Any]:
    openings: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    ids_by_path: dict[tuple[str, ...], list[str]] = defaultdict(list)
    ids_by_position: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        try:
            san_moves, uci_moves, board = _parse_line(row["pgn"])
            family, variations = _name_parts(row["name"])
            opening_id = _opening_id(row["eco"], row["name"], uci_moves)
            position_key = _position_key(board)
            entry = {
                "openingId": opening_id,
                "eco": row["eco"],
                "name": row["name"],
                "familyName": family,
                "variationPath": variations,
                "pgn": row["pgn"],
                "sanMoves": san_moves,
                "uciMoves": uci_moves,
                "plyCount": len(uci_moves),
                "terminalFen": board.fen(),
                "canonicalPositionKey": position_key,
                "parentOpeningIds": [],
                "transpositionOpeningIds": [],
            }
            openings.append(entry)
            ids_by_path[tuple(uci_moves)].append(opening_id)
            ids_by_position[position_key].append(opening_id)
        except (KeyError, ValueError, chess.InvalidMoveError) as exc:
            rejected.append({"eco": row.get("eco", ""), "name": row.get("name", ""), "error": str(exc)})

    for entry in openings:
        path = tuple(entry["uciMoves"])
        for length in range(len(path) - 1, 0, -1):
            parents = ids_by_path.get(path[:length])
            if parents:
                entry["parentOpeningIds"] = sorted(parents)
                break
        peers = ids_by_position[entry["canonicalPositionKey"]]
        entry["transpositionOpeningIds"] = sorted(
            opening_id for opening_id in peers if opening_id != entry["openingId"]
        )

    openings.sort(key=lambda item: (item["eco"], item["name"], item["plyCount"], item["openingId"]))
    transposition_groups = sum(1 for ids in ids_by_position.values() if len(ids) > 1)
    return {
        "schemaVersion": "phase8a-opening-path-catalog-1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "title": "Lichess chess-openings",
            "url": SOURCE_URL,
            "revision": revision,
            "license": "CC0-1.0",
            "authorityBoundary": (
                "This catalog supplies opening names, ECO codes and verified move paths only; "
                "it does not supply a current engine evaluation or prove a strategic claim."
            ),
        },
        "summary": {
            "inputRows": len(rows),
            "acceptedOpenings": len(openings),
            "rejectedRows": len(rejected),
            "ecoCodes": len({entry["eco"] for entry in openings}),
            "families": len({entry["familyName"] for entry in openings}),
            "canonicalPositions": len(ids_by_position),
            "transpositionGroups": transposition_groups,
            "maxPly": max((entry["plyCount"] for entry in openings), default=0),
        },
        "rejected": rejected,
        "openings": openings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--runtime-output", type=Path, default=DEFAULT_RUNTIME_OUTPUT)
    args = parser.parse_args()
    source = args.source if args.source.is_absolute() else ROOT / args.source
    output = args.output if args.output.is_absolute() else ROOT / args.output
    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    runtime_output = (
        args.runtime_output if args.runtime_output.is_absolute() else ROOT / args.runtime_output
    )
    catalog = build_catalog(load_rows(source), revision=_source_revision(source))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    runtime_output.parent.mkdir(parents=True, exist_ok=True)
    runtime_output.write_text(
        json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    manifest.write_text(json.dumps({
        "schemaVersion": "phase8a-opening-path-manifest-1.0",
        "generatedAt": catalog["generatedAt"],
        "source": catalog["source"],
        "summary": catalog["summary"],
        "output": str(output.relative_to(ROOT)).replace("\\", "/"),
        "runtimeOutput": str(runtime_output.relative_to(ROOT)).replace("\\", "/"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(catalog["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
