from __future__ import annotations

import json
import statistics
from pathlib import Path

import chess


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "work" / "research_books" / "phase8a_sources" / "kling-fenshot-results.json"
ADJUDICATION = ROOT / "docs" / "research" / "phase8c-kling-human-review-adjudication.json"
OUTPUT = ROOT / "docs" / "research" / "phase8c-kling-calibrated-candidates.json"
CONFIDENCE_WEIGHT = 0.5


def normalized_corners(read: dict, width: int, height: int) -> list[float]:
    corners = read["corners"]
    return [
        corners["x0"] / width,
        corners["y0"] / height,
        corners["x1"] / width,
        corners["y1"] / height,
    ]


def calibration_profile(rows: list[dict], gold: dict[str, str]) -> list[float]:
    landmarks = []
    for row in rows:
        exact = [read for read in row["bookReads"] if read["placement"] == gold.get(row["file"])]
        if not exact:
            continue
        best = max(exact, key=lambda read: read["meanConfidence"] + 0.15 * read["minConfidence"])
        landmarks.append(normalized_corners(best, row["width"], row["height"]))
    if not landmarks:
        raise ValueError("No exact reviewed candidates are available for calibration")
    return [statistics.median(values) for values in zip(*landmarks)]


def candidate_score(read: dict, width: int, height: int, profile: list[float]) -> float:
    geometry_distance = sum(
        abs(actual - expected)
        for actual, expected in zip(normalized_corners(read, width, height), profile)
    )
    confidence = read["meanConfidence"] + 0.2 * read["minConfidence"]
    return -geometry_distance + CONFIDENCE_WEIGHT * confidence


def select_candidate(row: dict, profile: list[float]) -> dict:
    scored = []
    for read in row.get("bookReads", []):
        scored.append({
            **read,
            "selectionScore": candidate_score(read, row["width"], row["height"], profile),
        })
    if not scored:
        raise ValueError(f"No board candidates for {row['file']}")
    scored.sort(key=lambda item: item["selectionScore"], reverse=True)
    best = scored[0]
    next_distinct = next(
        (item for item in scored[1:] if item["placement"] != best["placement"]),
        None,
    )
    return {
        **best,
        "selectionMargin": (
            best["selectionScore"] - next_distinct["selectionScore"]
            if next_distinct else None
        ),
    }


def valid_turns(placement: str) -> list[str]:
    turns = []
    for turn in ("w", "b"):
        try:
            board = chess.Board(f"{placement} {turn} - - 0 1")
        except ValueError:
            continue
        if board.is_valid():
            turns.append(turn)
    return turns


def leave_one_out(rows: list[dict], gold: dict[str, str]) -> dict:
    reviewed = [row for row in rows if row["file"] in gold]
    correct = 0
    details = []
    for held_out in reviewed:
        training_gold = {key: value for key, value in gold.items() if key != held_out["file"]}
        profile = calibration_profile(reviewed, training_gold)
        selected = select_candidate(held_out, profile)
        matched = selected["placement"] == gold[held_out["file"]]
        correct += int(matched)
        details.append({"file": held_out["file"], "matched": matched})
    return {"correct": correct, "total": len(reviewed), "details": details}


def build_output(rows: list[dict], adjudication: dict) -> dict:
    gold = {item["file"]: item["placement"] for item in adjudication["positions"]}
    profile = calibration_profile(rows, gold)
    selected = []
    for row in rows:
        if not row.get("bookReads"):
            continue
        candidate = select_candidate(row, profile)
        placement = candidate["placement"]
        selected.append({
            "file": row["file"],
            "placement": placement,
            "corners": candidate["corners"],
            "variant": candidate["variant"],
            "minConfidence": candidate["minConfidence"],
            "meanConfidence": candidate["meanConfidence"],
            "selectionScore": candidate["selectionScore"],
            "selectionMargin": candidate["selectionMargin"],
            "validTurns": valid_turns(placement),
            "reviewed": row["file"] in gold,
            "matchesReviewedPlacement": (
                placement == gold[row["file"]] if row["file"] in gold else None
            ),
        })
    return {
        "schemaVersion": "phase8c-kling-calibrated-recovery-1.0",
        "method": "reviewed_layout_geometry_plus_tile_model_confidence",
        "confidenceWeight": CONFIDENCE_WEIGHT,
        "calibrationProfile": profile,
        "leaveOneOut": leave_one_out(rows, gold),
        "positions": selected,
    }


def main() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    adjudication = json.loads(ADJUDICATION.read_text(encoding="utf-8"))
    output = build_output(payload["results"], adjudication)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "positions": len(output["positions"]),
        "leaveOneOut": output["leaveOneOut"],
        "output": str(OUTPUT),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
