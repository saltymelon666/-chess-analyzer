import json
from pathlib import Path

import pytest

from app.commentary_quality import (
    CommentaryClaim,
    CoreRouteGoldSpec,
    GoldCommentaryAtom,
    build_core_route_gold_atoms,
    evaluate_commentary_quality,
)
from scripts.run_professional_quality_suite import load_gold_atoms


def test_commentary_quality_separates_reference_precision_from_gold_recall() -> None:
    claims = [
        CommentaryClaim(
            path="playedMoveAnalysis.evaluationReason",
            kind="verdict",
            text="实战选择没有解决当前最紧迫的问题。",
            evidence_refs=["move:played", "line:1"],
        ),
        CommentaryClaim(
            path="comparison.mainDifference",
            kind="mechanism",
            text="第一路线先完成关键协调。",
            evidence_refs=["line:1", "plan:white:1"],
        ),
        CommentaryClaim(
            path="mainDanger.description",
            kind="opponent_resource",
            text="对手有直接反制资源。",
            evidence_refs=["unknown:1"],
        ),
    ]
    gold = [
        GoldCommentaryAtom(
            atomId="verdict",
            kind="verdict",
            summary="正确评价实战着",
            acceptableEvidenceSets=[["move:played", "line:1"]],
            acceptableClaimPaths=["playedMoveAnalysis.evaluationReason"],
            requiredTextGroups=[["实战选择"]],
        ),
        GoldCommentaryAtom(
            atomId="mechanism",
            kind="mechanism",
            summary="说明第一路线解决的核心机制",
            acceptableEvidenceSets=[["line:1", "plan:white:1"]],
            acceptableClaimPaths=["comparison.mainDifference"],
            requiredTextGroups=[["关键协调"]],
        ),
        GoldCommentaryAtom(
            atomId="resource",
            kind="opponent_resource",
            summary="指出对手的可靠反制",
            acceptableEvidenceSets=[["threat:black:1"]],
            acceptableClaimPaths=["mainDanger.description"],
            requiredTextGroups=[["反制"]],
        ),
    ]

    metrics = evaluate_commentary_quality(
        claims,
        gold,
        allowed_evidence_refs={"move:played", "line:1", "plan:white:1", "threat:black:1"},
    )

    assert metrics.reference_precision == 2 / 3
    assert metrics.gold_atom_recall == 2 / 3
    assert metrics.required_detail_count == 3
    assert metrics.detail_recall == 2 / 3
    assert metrics.core_recall is None
    assert metrics.required_by_kind == {
        "verdict": 1,
        "mechanism": 1,
        "opponent_resource": 1,
    }
    assert metrics.covered_by_kind["opponent_resource"] == 0
    assert metrics.unmatched_required_atoms == ["resource"]
    assert metrics.unsupported_claim_paths == ["mainDanger.description"]


def test_optional_gold_atoms_do_not_lower_required_recall() -> None:
    metrics = evaluate_commentary_quality(
        [
            CommentaryClaim(
                path="comparison.mainDifference",
                kind="mechanism",
                text="首选路线优先处理当前任务。",
                evidence_refs=["line:1"],
            )
        ],
        [
            GoldCommentaryAtom(
                atomId="required",
                kind="mechanism",
                summary="首选路线机制",
                acceptableEvidenceSets=[["line:1"]],
                acceptableClaimPaths=["comparison.mainDifference"],
                requiredTextGroups=[["首选路线"]],
            ),
            GoldCommentaryAtom(
                atomId="optional",
                kind="practical_plan",
                summary="补充计划",
                acceptableEvidenceSets=[["plan:white:1"]],
                acceptableClaimPaths=["plans.white[*].description"],
                requiredTextGroups=[["计划"]],
                required=False,
            ),
        ],
        allowed_evidence_refs={"line:1", "plan:white:1"},
    )

    assert metrics.required_gold_count == 1
    assert metrics.covered_gold_count == 1
    assert metrics.gold_atom_recall == 1.0


def test_gold_atom_requires_path_and_semantic_terms_not_only_matching_refs() -> None:
    atom = GoldCommentaryAtom(
        atomId="double-attack",
        kind="mechanism",
        summary="说明Ng5形成双攻",
        acceptableEvidenceSets=[["fact:tactic:ng5"]],
        acceptableClaimPaths=["playedMoveAnalysis.intention"],
        requiredTextGroups=[["Ng5"], ["同时攻击", "双攻"], ["黑后"], ["黑象"]],
    )
    wrong_path = CommentaryClaim(
        path="candidateLines[0].directPurpose",
        kind="mechanism",
        text="Ng5作为首选路线的起点。",
        evidence_refs=["fact:tactic:ng5"],
    )
    missing_meaning = CommentaryClaim(
        path="playedMoveAnalysis.intention",
        kind="mechanism",
        text="Ng5作为首选路线的起点。",
        evidence_refs=["fact:tactic:ng5"],
    )
    covered = CommentaryClaim(
        path="playedMoveAnalysis.intention",
        kind="mechanism",
        text="Ng5形成双攻，同时攻击黑后与黑象。",
        evidence_refs=["fact:tactic:ng5"],
    )

    missed = evaluate_commentary_quality(
        [wrong_path, missing_meaning],
        [atom],
        allowed_evidence_refs={"fact:tactic:ng5"},
    )
    matched = evaluate_commentary_quality(
        [covered],
        [atom],
        allowed_evidence_refs={"fact:tactic:ng5"},
    )

    assert missed.gold_atom_recall == 0.0
    assert missed.unmatched_required_atoms == ["double-attack"]
    assert matched.gold_atom_recall == 1.0


def test_gold_atom_path_matching_treats_numeric_index_literally() -> None:
    atom = GoldCommentaryAtom(
        atomId="response",
        kind="opponent_resource",
        summary="首选路线的对手回应",
        acceptableEvidenceSets=[["line:1"]],
        acceptableClaimPaths=["candidateLines[0].opponentResponse"],
        requiredTextGroups=[["Qxf7"]],
    )
    claims = [
        CommentaryClaim(
            path="candidateLines[0].opponentResponse",
            kind="opponent_resource",
            text="Qxf7",
            evidence_refs=["line:1"],
        )
    ]

    metrics = evaluate_commentary_quality(
        claims,
        [atom],
        allowed_evidence_refs={"line:1"},
    )

    assert metrics.gold_atom_recall == 1.0


def test_commentary_quality_reports_core_and_detail_recall_separately() -> None:
    claims = [
        CommentaryClaim(
            path="playedMoveAnalysis.evaluationReason",
            kind="verdict",
            text="实战着e4与Stockfish首选一致。",
            evidence_refs=["move:played:1"],
        )
    ]
    atoms = [
        GoldCommentaryAtom(
            atomId="core-verdict",
            kind="verdict",
            tier="core",
            summary="实战着评价",
            acceptableEvidenceSets=[["move:played:1"]],
            acceptableClaimPaths=["playedMoveAnalysis.evaluationReason"],
            requiredTextGroups=[["e4"], ["首选一致"]],
        ),
        GoldCommentaryAtom(
            atomId="detail-mechanism",
            kind="mechanism",
            tier="detail",
            summary="中心突破机制",
            acceptableEvidenceSets=[["line:1"]],
            acceptableClaimPaths=["candidateLines[0].directPurpose"],
            requiredTextGroups=[["中心突破"]],
        ),
    ]

    metrics = evaluate_commentary_quality(
        claims,
        atoms,
        allowed_evidence_refs={"move:played:1", "line:1"},
    )

    assert metrics.core_recall == 1.0
    assert metrics.detail_recall == 0.0
    assert metrics.gold_atom_recall == 0.5
    assert metrics.recall_by_kind == {"verdict": 1.0, "mechanism": 0.0}


@pytest.mark.parametrize(
    ("played_is_best", "text"),
    [
        (True, "e4就是引擎首选，这一步没有问题。"),
        (False, "e3和c4都可以，评价差距很小，只是选择的侧重点不同。"),
        (False, "Qe4不算大错，但比Ne4差约0.85兵。"),
    ],
)
def test_core_verdict_accepts_current_deterministic_wording(
    played_is_best: bool,
    text: str,
) -> None:
    spec = CoreRouteGoldSpec(
        playedMove="e4" if played_is_best else ("Qe4" if text.startswith("Qe4") else "e3"),
        bestMove="e4",
        opponentResponse="e5",
        playedIsBest=played_is_best,
    )
    atom = build_core_route_gold_atoms("position", spec)[0]
    metrics = evaluate_commentary_quality(
        [CommentaryClaim(
            path="playedMoveAnalysis.evaluationReason",
            kind="verdict",
            text=text,
            evidence_refs=["move:played:1", "evaluation:before:1", "evaluation:after:1"],
        )],
        [atom],
        allowed_evidence_refs={
            "move:played:1",
            "evaluation:before:1",
            "evaluation:after:1",
        },
    )

    assert metrics.gold_atom_recall == 1.0


def test_commentary_gold_v1_is_explicitly_scoped_and_schema_valid() -> None:
    path = Path("tests/fixtures/commentary_gold_atoms_v1.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    positions = document["positions"]
    position_ids = [item["id"] for item in positions]
    atoms = [
        GoldCommentaryAtom.model_validate(atom)
        for item in positions
        for atom in item["goldAtoms"]
    ]

    assert document["version"] == "commentary-gold-v1"
    assert "不使用待评价的DeepSeek正文" in document["annotationMethod"]
    assert position_ids == ["tactic-2", "king-attack-2", "simplify-1"]
    assert len(atoms) == 12
    assert len({atom.atom_id for atom in atoms}) == len(atoms)


def test_commentary_gold_v2_covers_all_validation_positions_in_two_tiers() -> None:
    gold_document = json.loads(
        Path("tests/fixtures/commentary_gold_atoms_v2.json").read_text(encoding="utf-8")
    )
    validation_positions = json.loads(
        Path("tests/fixtures/professional_validation_positions.json").read_text(
            encoding="utf-8"
        )
    )
    fixtures_by_id = {item["id"]: item for item in validation_positions}
    atoms: list[GoldCommentaryAtom] = []

    for item in gold_document["positions"]:
        fixture = fixtures_by_id[item["id"]]
        spec = CoreRouteGoldSpec.model_validate(item["coreRoute"])
        atoms.extend(build_core_route_gold_atoms(item["id"], spec))
        atoms.extend(
            GoldCommentaryAtom.model_validate(atom)
            for atom in item.get("detailAtoms", [])
        )
        assert spec.played_move == fixture["playedMove"]["san"]
        assert spec.best_move == fixture["stockfishLines"][0]["plies"][0]["san"]
        assert spec.opponent_response == fixture["stockfishLines"][0]["plies"][1]["san"]
        assert spec.played_is_best == (spec.played_move == spec.best_move)

    assert gold_document["version"] == "commentary-gold-v2"
    assert "不使用待评价的DeepSeek正文" in gold_document["annotationMethod"]
    assert {item["id"] for item in gold_document["positions"]} == set(fixtures_by_id)
    assert len([atom for atom in atoms if atom.tier == "core"]) == 45
    assert len([atom for atom in atoms if atom.tier == "detail"]) == 6
    assert len({atom.atom_id for atom in atoms}) == len(atoms) == 51


def test_commentary_gold_v3_adds_only_high_confidence_verified_plan_atoms() -> None:
    loaded = load_gold_atoms(Path("tests/fixtures/commentary_gold_atoms_v3.json"))
    fixtures = json.loads(
        Path("tests/fixtures/professional_validation_positions.json").read_text(
            encoding="utf-8"
        )
    )
    atoms = [
        GoldCommentaryAtom.model_validate(atom)
        for position_atoms in loaded.values()
        for atom in position_atoms
    ]

    assert set(loaded) == {item["id"] for item in fixtures}
    assert len([atom for atom in atoms if atom.tier == "core"]) == 45
    assert len([atom for atom in atoms if atom.tier == "detail"]) == 23
    assert len(atoms) == 68
    plan_atoms = [atom for atom in atoms if "-plan-" in atom.atom_id]
    assert len(plan_atoms) == 17
    assert all(atom.kind == "practical_plan" for atom in plan_atoms)
    assert all(
        atom.acceptable_claim_paths in (
            ["plans.white[*].description"],
            ["plans.black[*].description"],
        )
        for atom in plan_atoms
    )
