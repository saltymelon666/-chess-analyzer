from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import api
from app.analysis_focus import select_analysis_focus
from app.chess_facts import build_move_fact_package
from app.commentary_quality import (
    CoreRouteGoldSpec,
    GoldCommentaryAtom,
    build_core_route_gold_atoms,
    evaluate_commentary_quality,
    extract_commentary_claims,
)
from app.config import load_settings
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.professional_analysis import (
    ProfessionalAnalysisService,
    ProfessionalAttemptDiagnostic,
    compute_professional_complexity,
    professional_cache_key,
    build_professional_payload,
)
from app.narrative_claims import (
    LEGACY_NARRATIVE_MARKERS,
    NarrativeClaimPackage,
    evaluate_narrative_claim_grounding,
    resolve_narrative_claims,
)
from app.professional_validation import build_validation_context, validate_professional_analysis
from app.threat_analysis import ThreatAnalyzer, assess_initiative
from app.unified_book_knowledge import UnifiedBookKnowledgeRepository


DEFAULT_FIXTURES = Path("tests/fixtures/professional_validation_positions.json")
DEFAULT_GOLD_ATOMS = Path("tests/fixtures/commentary_gold_atoms_v3.json")
DEFAULT_RESULTS = Path("docs/professional-analysis-quality-results.json")
DEFAULT_REPORT = Path("docs/professional-analysis-quality-report.md")


def focus_summary(move: Any) -> dict[str, Any]:
    focus = select_analysis_focus(move)
    old_candidates = {"white": [], "black": []}
    for fact in [*move.position_facts.piece_activity, *move.position_facts.pawn_structure]:
        if fact.side in old_candidates and fact.category in {
            "undefended_piece", "underprotected", "isolated_pawn",
            "doubled_pawns", "vulnerable_pawn",
        }:
            old_candidates[fact.side].append(fact.description)
    old_weaknesses = {
        side: items[:1]
        for side, items in old_candidates.items()
    }
    old_threats = [fact.description for fact in move.position_facts.threats[:1]]
    after_weaknesses = {
        side: [item.description for item in items]
        for side, items in focus.weaknesses.items()
    }
    return {
        "beforeWeaknessCount": sum(len(items) for items in old_weaknesses.values()),
        "afterWeaknessCount": sum(len(items) for items in after_weaknesses.values()),
        "filteredUndefendedOnly": focus.counters["filteredUndefendedOnly"],
        "filteredKingSafety": focus.counters["filteredKingSafety"],
        "movedToCandidateLine": focus.counters["movedToCandidateLine"],
        "filteredOrdinaryPvCaptures": focus.counters["filteredOrdinaryPvCaptures"],
        "fixedMaterialSectionRemoved": True,
        "displayedSections": list(focus.display_sections),
        "beforeWeaknesses": old_weaknesses,
        "afterWeaknesses": after_weaknesses,
        "beforeKingSafety": [fact.description for fact in move.position_facts.king_safety],
        "afterKingSafety": [
            item.description for item in focus.selected_facts
            if item.display_section == "kingSafety"
        ],
        "beforeGlobalThreats": old_threats,
        "afterGlobalThreats": [item.description for item in focus.global_threats],
        "candidateLineEvents": {
            str(rank): [item.description for item in items]
            for rank, items in focus.line_events.items()
        },
        "meaninglessWeaknessStillDisplayed": any(
            any(token in item.description for token in ("白马(g5)", "黑兵(a7)", "白象(h4)", "黑象(b7)", "白马g5", "黑兵a7", "白象h4", "黑象b7"))
            for items in focus.weaknesses.values()
            for item in items
        ),
    }


def valid_plans(analysis: Any, allowed_refs: set[str]) -> bool:
    plans = [*analysis.plans.white, *analysis.plans.black]
    return all(
        item.evidence_refs and set(item.evidence_refs) <= allowed_refs for item in plans
    )


def valid_danger(analysis: Any, allowed_refs: set[str]) -> bool:
    return bool(analysis.main_danger.description.strip()) and bool(
        analysis.main_danger.evidence_refs
    ) and set(analysis.main_danger.evidence_refs) <= allowed_refs


def valid_routes(move: Any, analysis: Any) -> bool:
    return (
        len(analysis.candidate_lines) == len(move.candidate_lines) == 3
        and [item.first_move for item in analysis.candidate_lines]
        == [item.first_move.san for item in move.candidate_lines]
    )


def thought_path_summary(
    move: Any,
    analysis: Any,
    package: NarrativeClaimPackage,
) -> dict[str, Any]:
    selected = resolve_narrative_claims(
        package,
        analysis.played_move_analysis.claim_refs,
    )
    core = analysis.played_move_analysis.intention
    kinds = {item.kind for item in selected}
    inferior = bool(
        move.best_move_uci
        and move.played_move.uci != move.best_move_uci
        and move.centipawn_loss is not None
        and move.centipawn_loss >= 50
    )
    reply_claims = [item for item in selected if item.kind == "opponent_resource"]
    punishment_mode = "not_applicable"
    punishment_handled = not inferior
    if inferior and any(
        "现有数据不足以证明这次吃子解释了全部分差" in item.statement
        for item in reply_claims
    ):
        punishment_mode = "immediate_capture"
        punishment_handled = True
    elif inferior and any(
        "不能倒推成第一回应的直接效果" in item.statement for item in reply_claims
    ):
        punishment_mode = "route_boundary"
        punishment_handled = True
    global_posture_claim = next((
        item
        for item in selected
        if item.claim_id.endswith(":position:evaluation")
        and item.kind == "position_fact"
        and item.source == "stockfish"
        and item.scope == "before_move"
    ), None)
    played_claim = next((item for item in selected if item.claim_id.endswith(":played")), None)
    comparison_claim = next((
        item for item in selected if item.kind == "evaluation_comparison"
    ), None)
    teaching_claim = next((item for item in selected if item.kind == "teaching_rule"), None)
    indices = {
        "global": core.find(global_posture_claim.statement) if global_posture_claim else -1,
        "played": core.find(played_claim.statement) if played_claim else -1,
        "comparison": core.find(comparison_claim.statement) if comparison_claim else -1,
        "teaching": core.find(teaching_claim.statement) if teaching_claim else -1,
    }
    global_posture = global_posture_claim is not None and indices["global"] == 0
    key_choice = (
        "evaluation_comparison" in kinds
        and min(indices["played"], indices["comparison"]) >= 0
        and indices["played"] < indices["comparison"]
    )
    teaching_summary = (
        "teaching_rule" in kinds
        and indices["teaching"] > indices["comparison"] >= 0
    )
    legacy_template_free = not any(marker in core for marker in LEGACY_NARRATIVE_MARKERS)
    return {
        "globalPosture": global_posture,
        "keyChoice": key_choice,
        "teachingSummary": teaching_summary,
        "inferiorMove": inferior,
        "punishmentHandled": punishment_handled,
        "punishmentMode": punishment_mode,
        "legacyTemplateFree": legacy_template_free,
        "complete": (
            global_posture
            and key_choice
            and teaching_summary
            and punishment_handled
            and legacy_template_free
        ),
    }


def cache_latency_ms(move: Any, generated: Any) -> int:
    analysis_id = "professional-quality-cache-check"
    depth = max(line.depth for line in move.candidate_lines)
    key = professional_cache_key(
        move,
        stockfish_version="Stockfish 18",
        stockfish_depth=depth,
    )
    api.game_cache[analysis_id] = [move]
    api.professional_cache[key] = generated
    client = TestClient(api.app)
    started = time.perf_counter()
    response = client.post(
        "/api/professional-analysis",
        json={"analysis_id": analysis_id, "move_index": 1},
    )
    elapsed = round((time.perf_counter() - started) * 1000)
    api.game_cache.pop(analysis_id, None)
    api.professional_cache.pop(key, None)
    if response.status_code != 200 or response.json().get("cached") is not True:
        raise RuntimeError("本地缓存路径未返回cached=true")
    return elapsed


def load_gold_atoms(path: Path) -> dict[str, list[dict[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[dict[str, Any]]] = {}
    base_file = document.get("baseFile")
    if base_file:
        result = load_gold_atoms(path.parent / base_file)
    for item in document["positions"]:
        atoms = result.setdefault(item["id"], [])
        if "coreRoute" in item:
            atoms.extend(
                atom.model_dump(by_alias=True)
                for atom in build_core_route_gold_atoms(
                    item["id"],
                    CoreRouteGoldSpec.model_validate(item["coreRoute"]),
                )
            )
        atoms.extend(item.get("detailAtoms", []))
    return result


async def run_suite(
    fixtures_path: Path,
    gold_atoms_path: Path | None,
    results_path: Path,
    report_path: Path,
    selected_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    gold_atoms_by_id: dict[str, list[dict[str, Any]]] = {}
    if gold_atoms_path is not None and gold_atoms_path.exists():
        gold_atoms_by_id = load_gold_atoms(gold_atoms_path)
    if selected_ids:
        fixtures = [item for item in fixtures if item["id"] in selected_ids]
    settings = load_settings()
    if not settings.deepseek_api_key:
        raise RuntimeError("未配置DeepSeek API Key")
    engine = StockfishService(
        settings.stockfish_path,
        depth=10,
        threads=1,
        hash_mb=32,
        multipv=3,
        timeout_seconds=60,
    )
    service = ProfessionalAnalysisService(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_seconds=settings.deepseek_timeout_seconds,
        book_knowledge=UnifiedBookKnowledgeRepository(),
    )
    results: list[dict[str, Any]] = []
    first_generated = None
    first_move = None

    for index, fixture in enumerate(fixtures, 1):
        print(f"[{index}/{len(fixtures)}] {fixture['id']} starting", flush=True)
        review = await analyze_pgn(
            pgn=fixture["pgn"],
            stockfish=engine,
            analysis_id=f"quality-{fixture['id']}",
            depth=10,
            timeout_seconds=90,
            max_plies=2,
        )
        move = review.moves[0]
        diagnostics: list[ProfessionalAttemptDiagnostic] = []
        started = time.perf_counter()
        row: dict[str, Any] = {
            "id": fixture["id"],
            "category": fixture["category"],
            "complexity": fixture["complexity"],
            "httpEquivalent": 200,
            **focus_summary(move),
        }
        try:
            fact_package = build_move_fact_package(move)
            threat_package = await ThreatAnalyzer().analyze(
                fact_package,
                stockfish=engine,
            )
            generated = await service.analyze(
                move,
                diagnostics=diagnostics,
                threat_package=threat_package,
                opening_context=move.opening_context,
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            initiative = assess_initiative(fact_package, threat_package)
            context = build_validation_context(
                move,
                generated.analysis.complexity,
                initiative_side=initiative.side,
                threat_package=threat_package,
            )
            final_errors = validate_professional_analysis(generated.analysis, context)
            accepted_attempt = next((item.attempt for item in diagnostics if item.accepted), None)
            output_text = json.dumps(generated.analysis.model_dump(by_alias=True), ensure_ascii=False)
            gold_atoms = [
                GoldCommentaryAtom.model_validate(item)
                for item in gold_atoms_by_id.get(
                    fixture["id"],
                    fixture.get("goldAtoms", []),
                )
            ]
            atomic_quality = evaluate_commentary_quality(
                extract_commentary_claims(generated.analysis),
                gold_atoms,
                allowed_evidence_refs=context.allowed_evidence_ids,
            )
            claim_payload = build_professional_payload(
                move,
                compute_professional_complexity(move),
                context.allowed_evidence_ids,
                fact_package=fact_package,
                threat_package=threat_package,
            )
            claim_package = NarrativeClaimPackage.model_validate(
                claim_payload["narrativeClaims"],
            )
            claim_grounding = evaluate_narrative_claim_grounding(
                generated.analysis,
                claim_package,
            )
            thought_path = thought_path_summary(
                move,
                generated.analysis,
                claim_package,
            )
            row.update({
                "firstPass": accepted_attempt == 1,
                "retried": len(diagnostics) > 1,
                "fallback": bool(generated.validation_warnings),
                "finalValid": not final_errors,
                "inputTokens": diagnostics[0].prompt_tokens if diagnostics else None,
                "outputTokens": generated.usage.completion_tokens,
                "totalTokens": generated.usage.total_tokens,
                "firstLatencyMs": diagnostics[0].network_ms if diagnostics else elapsed_ms,
                "totalLatencyMs": elapsed_ms,
                "networkMs": generated.usage.network_ms,
                "validationMs": generated.usage.validation_ms,
                "postprocessMs": generated.usage.postprocess_ms,
                "chineseChars": len(re.findall(r"[\u4e00-\u9fff]", output_text)),
                "analysis": generated.analysis.model_dump(by_alias=True),
                "dangerHasEvidence": valid_danger(generated.analysis, context.allowed_evidence_ids),
                "plansHaveEvidence": valid_plans(generated.analysis, context.allowed_evidence_ids),
                "threeRoutesValid": valid_routes(move, generated.analysis),
                "atomicQuality": atomic_quality.model_dump(by_alias=True),
                "narrativeClaimGrounding": claim_grounding.model_dump(by_alias=True),
                "thoughtPath": thought_path,
                "issues": [
                    {
                        "attempt": item.attempt,
                        "path": issue.path,
                        "category": issue.category,
                        "message": issue.message,
                    }
                    for item in diagnostics
                    for issue in item.issues
                ],
                "normalizations": [
                    {
                        "attempt": item.attempt,
                        "path": issue.path,
                        "category": issue.category,
                        "message": issue.message,
                    }
                    for item in diagnostics
                    for issue in item.normalizations
                ],
                "finalErrors": final_errors,
            })
            if first_generated is None:
                first_generated = generated
                first_move = move
        except Exception as exc:
            row.update({
                "firstPass": False,
                "retried": len(diagnostics) > 1,
                "fallback": False,
                "finalValid": False,
                "inputTokens": diagnostics[0].prompt_tokens if diagnostics else None,
                "outputTokens": sum(item.completion_tokens or 0 for item in diagnostics),
                "totalTokens": sum(item.total_tokens or 0 for item in diagnostics),
                "firstLatencyMs": diagnostics[0].network_ms if diagnostics else None,
                "totalLatencyMs": round((time.perf_counter() - started) * 1000),
                "dangerHasEvidence": False,
                "plansHaveEvidence": False,
                "threeRoutesValid": False,
                "issues": [{"path": "$network", "category": type(exc).__name__, "message": str(exc)}],
                "finalErrors": [str(exc)],
            })
        results.append(row)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"[{index}/{len(fixtures)}] {fixture['id']} "
            f"first={row['firstPass']} fallback={row['fallback']} "
            f"tokens={row.get('inputTokens')} first_ms={row.get('firstLatencyMs')}",
            flush=True,
        )

    cache_ms = cache_latency_ms(first_move, first_generated) if first_move and first_generated else None
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(results, cache_ms, settings.deepseek_model), encoding="utf-8")
    print(f"cache_ms={cache_ms}; report={report_path}", flush=True)
    return results


def render_report(results: list[dict[str, Any]], cache_ms: int | None, model: str) -> str:
    count = len(results)
    first_passes = sum(bool(item["firstPass"]) for item in results)
    final_valid = sum(bool(item["finalValid"]) for item in results)
    fallbacks = sum(bool(item["fallback"]) for item in results)
    first_rate = first_passes / count * 100 if count else 0
    final_rate = final_valid / count * 100 if count else 0
    fallback_rate = fallbacks / count * 100 if count else 0
    atom_rows = [
        item["atomicQuality"]
        for item in results
        if item.get("atomicQuality", {}).get("requiredGoldCount", 0) > 0
    ]
    if atom_rows:
        total_claims = sum(item["claimCount"] for item in atom_rows)
        supported_claims = sum(item["supportedClaimCount"] for item in atom_rows)
        required_atoms = sum(item["requiredGoldCount"] for item in atom_rows)
        covered_atoms = sum(item["coveredGoldCount"] for item in atom_rows)
        required_core = sum(item["requiredCoreCount"] for item in atom_rows)
        covered_core = sum(item["coveredCoreCount"] for item in atom_rows)
        required_detail = sum(item["requiredDetailCount"] for item in atom_rows)
        covered_detail = sum(item["coveredDetailCount"] for item in atom_rows)
        required_plans = sum(
            item.get("requiredByKind", {}).get("practical_plan", 0)
            for item in atom_rows
        )
        covered_plans = sum(
            item.get("coveredByKind", {}).get("practical_plan", 0)
            for item in atom_rows
        )
        atomic_summary = (
            f"- 人工金标覆盖局面：{len(atom_rows)}/{count}\n"
            f"- 结构化声明引用ID合法率：{supported_claims / max(1, total_claims):.1%}\n"
            f"- 人工金标关键原子召回：{covered_atoms / max(1, required_atoms):.1%}\n"
            f"- 核心原子召回：{covered_core}/{required_core}"
            + (
                f"（{covered_core / required_core:.1%}）\n"
                if required_core else "（未标注）\n"
            )
            + f"- 细节原子召回：{covered_detail}/{required_detail}"
            + (
                f"（{covered_detail / required_detail:.1%}）"
                if required_detail else "（未标注）"
            )
            + f"\n- 已验证计划原子召回：{covered_plans}/{required_plans}"
            + (
                f"（{covered_plans / required_plans:.1%}）"
                if required_plans else "（未标注）"
            )
        )
    else:
        atomic_summary = (
            "- 人工金标关键原子召回：未计算（质量集尚未提供经过人工审核的 `goldAtoms`，"
            "不能把结构校验冒充内容质量）。"
        )
    claim_rows = [
        item["narrativeClaimGrounding"]
        for item in results
        if item.get("narrativeClaimGrounding")
    ]
    grounded_claims = sum(item["entailedCount"] for item in claim_rows)
    declared_claims = sum(item["declaredCount"] for item in claim_rows)
    strict_claim_passes = sum(
        item["groundingPrecision"] == 1.0
        and item.get("exactRenderMatch") is True
        and not item.get("invalidClaimRefs")
        and not item.get("missingStatements")
        for item in claim_rows
    )
    claim_summary = (
        f"- 核心命题严格落地：{strict_claim_passes}/{len(claim_rows)}"
        f"（可评估覆盖{len(claim_rows)}/{count}）；"
        f"已声明命题逐字落地：{grounded_claims}/{declared_claims}"
        if claim_rows
        else "- 核心命题严格落地：未计算。"
    )
    thought_rows = [item["thoughtPath"] for item in results if item.get("thoughtPath")]
    complete_thought_paths = sum(bool(item["complete"]) for item in thought_rows)
    template_free_paths = sum(bool(item.get("legacyTemplateFree")) for item in thought_rows)
    inferior_rows = [item for item in thought_rows if item["inferiorMove"]]
    handled_punishments = sum(bool(item["punishmentHandled"]) for item in inferior_rows)
    immediate_capture_clues = sum(
        item["punishmentMode"] == "immediate_capture" for item in inferior_rows
    )
    bounded_punishments = sum(
        item["punishmentMode"] == "route_boundary" for item in inferior_rows
    )
    thought_summary = (
        f"- 强制思考路径完整：{complete_thought_paths}/{len(thought_rows)}"
        f"（可评估覆盖{len(thought_rows)}/{count}）。\n"
        f"- 固定栏目口号已清除：{template_free_paths}/{len(thought_rows)}。\n"
        f"- 次佳着惩罚处理：{handled_punishments}/{len(inferior_rows)}；"
        f"其中首应立即吃子线索{immediate_capture_clues}局，"
        f"严格保留路线边界{bounded_punishments}局。"
        if thought_rows
        else "- 强制思考路径完整：未计算。"
    )
    before_weaknesses = sum(item.get("beforeWeaknessCount", 0) for item in results)
    after_weaknesses = sum(item.get("afterWeaknessCount", 0) for item in results)
    filtered_undefended = sum(item.get("filteredUndefendedOnly", 0) for item in results)
    filtered_king = sum(item.get("filteredKingSafety", 0) for item in results)
    moved_line_events = sum(item.get("movedToCandidateLine", 0) for item in results)
    filtered_pv_captures = sum(item.get("filteredOrdinaryPvCaptures", 0) for item in results)
    table_rows = []
    for item in results:
        table_rows.append(
            "| {id} | {complexity} | {first} | {retry} | {fallback} | {input} | {output} | "
            "{latency} | {danger} | {plans} | {routes} | {claims} | {thought} |".format(
                id=item["id"],
                complexity=item["complexity"],
                first="是" if item["firstPass"] else "否",
                retry="是" if item["retried"] else "否",
                fallback="是" if item["fallback"] else "否",
                input=item.get("inputTokens") or "—",
                output=item.get("outputTokens") or "—",
                latency=item.get("firstLatencyMs") or "—",
                danger="是" if item["dangerHasEvidence"] else "否",
                plans="是" if item["plansHaveEvidence"] else "否",
                routes="是" if item["threeRoutesValid"] else "否",
                claims=(
                    "是"
                    if item.get("narrativeClaimGrounding", {}).get("groundingPrecision") == 1.0
                    and item.get("narrativeClaimGrounding", {}).get("exactRenderMatch") is True
                    else "否"
                ),
                thought="是" if item.get("thoughtPath", {}).get("complete") else "否",
            )
        )
    issue_rows = []
    for item in results:
        for issue in item.get("issues", []):
            issue_rows.append(
                f"- `{item['id']}` attempt {issue.get('attempt', '—')} `{issue['path']}` / "
                f"{issue['category']}：{issue['message']}"
            )
    if not issue_rows:
        issue_rows.append(f"- {count} 个局面均无原始输出校验错误。")
    normalization_rows = []
    for item in results:
        for issue in item.get("normalizations", []):
            normalization_rows.append(
                f"- `{item['id']}` attempt {issue.get('attempt', '—')} `{issue['path']}`：{issue['message']}"
            )
    if not normalization_rows:
        normalization_rows.append("- 无需移除事实包外棋盘字面量。")

    focus_rows = []
    for item in results:
        focus_rows.append(
            "| {id} | {before} | {after} | {undefended} | {king} | {moved} | {captures} | {sections} |".format(
                id=item["id"],
                before=item.get("beforeWeaknessCount", 0),
                after=item.get("afterWeaknessCount", 0),
                undefended=item.get("filteredUndefendedOnly", 0),
                king=item.get("filteredKingSafety", 0),
                moved=item.get("movedToCandidateLine", 0),
                captures=item.get("filteredOrdinaryPvCaptures", 0),
                sections="、".join(item.get("displayedSections", [])),
            )
        )

    comparison_blocks = []
    preferred = ["opening-1", "tactic-2", "closed-2"]
    selected = [next((item for item in results if item["id"] == wanted), None) for wanted in preferred]
    selected = [item for item in selected if item]
    if len(selected) < 3:
        selected.extend(item for item in results if item not in selected)
        selected = selected[:3]
    for item in selected:
        before_weak = [text for values in item.get("beforeWeaknesses", {}).values() for text in values] or ["无"]
        after_weak = [text for values in item.get("afterWeaknesses", {}).values() for text in values] or ["无"]
        before_threat = item.get("beforeGlobalThreats") or ["无"]
        after_threat = item.get("afterGlobalThreats") or ["无"]
        line_events = [
            f"路线{rank}：{text}"
            for rank, values in item.get("candidateLineEvents", {}).items()
            for text in values
        ] or ["无需要额外解释的路线内部事件"]
        comparison_blocks.append(
            f"### {item['id']}\n\n"
            f"- 修改前弱点：{'；'.join(before_weak)}\n"
            f"- 修改后弱点：{'；'.join(after_weak)}\n"
            f"- 修改前全局威胁：{'；'.join(before_threat)}\n"
            f"- 修改后全局威胁：{'；'.join(after_threat)}\n"
            f"- 路线内部事件：{'；'.join(line_events)}\n"
            f"- 最终栏目：{'、'.join(item.get('displayedSections', []))}"
        )

    return f"""# 专业棋局分析质量与性能报告

生成模型：`{model}`。本报告不包含 API Key、Authorization 请求头或任何密钥内容。

## game1 基线问题

旧版复杂局面输入为 80,620 Token，首次响应 75,136ms；两次原始输出均失败并使用安全回退。失败类型包括：

- 第一次：`candidateLines[*].firstMove / continuationPhases[*].moves` 出现不属于三条 Stockfish 路线的 `Bxh7+`、`Qxc3`、`Qxh2+`；`playedMoveAnalysis.positiveEffects` 把实战走法写成未验证吃子；`weaknesses.white[*].evidenceRefs` 引用了错误一方；`mainDanger` 缺少来源格和目标格，且描述了事实包中不存在的将军；正文 2,259 字，超过复杂局面上限。
- 第二次：多个 `evidenceRefs` 使用不存在的引用；候选路线中出现 `Bxe7`、`Bxh7+`、`Qxh2+`、`Rxb7`、`Rxh6`；`weaknesses.white[*].evidenceRefs` 黑白说反；再次描述不存在的将军；正文 2,260 字。
- 安全回退曾把结果 FEN 的一段 `p7` 误判成棋盘格；现已停止把结果 FEN写入正文，只保留结构化结果事实。

## 优化方案

- 候选路线只返回 `lineRef`；完整 PV 只返回已有 `plyRefs`，SAN、UCI、格子、棋子与结果局面由后端填充。
- 提示词不发送 FEN；仅发送 ChessFactPackage 版本/来源清单、去重棋子与事实引用、实战走法引用和三条最多 10 半回合的已验证路线；不发送 legalMoves、positionAfter、重复 evidence 字典、调试字段或整盘历史。
- 保持严格校验：未知 ID、事实包外格子、路线外 SAN、任何 UCI、黑白颠倒、缺证据结论仍会拒绝。
- 分别记录 DeepSeek 网络、校验和后处理耗时；保留缓存。

## {count} 局面结果

| 局面 | 复杂度 | 首次通过 | 重试 | 回退 | 输入Token | 输出Token | 首次耗时ms | 最大危险有证据 | 双方计划有证据 | 三条路线有效 | 核心命题落地 | 思考路径完整 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(table_rows)}

## 汇总

- 首次校验通过：{first_passes}/{count}（{first_rate:.1f}%）
- 最终严格校验通过：{final_valid}/{count}（{final_rate:.1f}%）
- 安全回退：{fallbacks}/{count}（{fallback_rate:.1f}%）
- 缓存响应：{cache_ms if cache_ms is not None else '未测得'}ms
{atomic_summary}
{claim_summary}
{thought_summary}

- 指标边界：引用ID合法率只检查引用是否进入允许目录；核心命题落地只检查 `playedMoveAnalysis.intention` 的完整程序渲染，均不等同于全部用户可见文字的专家事实准确率。

## 分析重点筛选验收

| 局面 | 修改前弱点 | 修改后弱点 | 过滤仅未保护 | 过滤无关王安全 | 移入候选路线 | 过滤普通PV吃子 | 最终展示栏目 |
|---|---:|---:|---:|---:|---:|---:|---|
{chr(10).join(focus_rows)}

- 修改前显示弱点：{before_weaknesses} 项；修改后：{after_weaknesses} 项。
- 被过滤的“仅未保护”事实：{filtered_undefended} 项。
- 被过滤的无关王安全描述：{filtered_king} 项。
- 从全局威胁归入候选路线内部：{moved_line_events} 项。
- 被过滤的普通PV吃子：{filtered_pv_captures} 项。
- 固定物质差栏目：已移除；物质事实仍保留在底层事实包和严格校验上下文。
- g5马、a7兵、h4象、b7象类无意义弱点：{'仍有，需要阻止合并' if any(item.get('meaninglessWeaknessStillDisplayed') for item in results) else '未再显示'}。

## 三个修改前后对比

{chr(10).join(comparison_blocks)}

## 原始输出校验明细

{chr(10).join(issue_rows)}

## 安全字面量归一化

以下项目不会被放行或返回给前端；后端先替换为“该格/该路线着法”，再执行完整严格校验：

{chr(10).join(normalization_rows)}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--gold-atoms", type=Path, default=DEFAULT_GOLD_ATOMS)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--ids", nargs="*", default=[])
    args = parser.parse_args()
    asyncio.run(run_suite(
        args.fixtures,
        args.gold_atoms,
        args.results,
        args.report,
        set(args.ids) or None,
    ))


if __name__ == "__main__":
    main()
