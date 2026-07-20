from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app import api
from app.config import load_settings
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.professional_analysis import (
    ProfessionalAnalysisService,
    ProfessionalAttemptDiagnostic,
    professional_cache_key,
)
from app.professional_validation import build_validation_context, validate_professional_analysis


DEFAULT_FIXTURES = Path("tests/fixtures/professional_validation_positions.json")
DEFAULT_RESULTS = Path("docs/professional-analysis-quality-results.json")
DEFAULT_REPORT = Path("docs/professional-analysis-quality-report.md")


def valid_key_pieces(move: Any, analysis: Any) -> bool:
    pieces = {
        (item["side"], item["piece"], item["square"])
        for item in move.position_facts.pieces
    }
    return all((item.side, item.piece, item.square) in pieces and item.evidence_refs for item in analysis.key_pieces)


def valid_plans(analysis: Any, allowed_refs: set[str]) -> bool:
    plans = [*analysis.plans.white, *analysis.plans.black]
    return bool(analysis.plans.white and analysis.plans.black) and all(
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


async def run_suite(
    fixtures_path: Path,
    results_path: Path,
    report_path: Path,
    selected_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
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
        }
        try:
            generated = await service.analyze(move, diagnostics=diagnostics)
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            context = build_validation_context(move, generated.analysis.complexity)
            final_errors = validate_professional_analysis(generated.analysis, context)
            accepted_attempt = next((item.attempt for item in diagnostics if item.accepted), None)
            output_text = json.dumps(generated.analysis.model_dump(by_alias=True), ensure_ascii=False)
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
                "keyPiecesCorrect": valid_key_pieces(move, generated.analysis),
                "dangerHasEvidence": valid_danger(generated.analysis, context.allowed_evidence_ids),
                "plansHaveEvidence": valid_plans(generated.analysis, context.allowed_evidence_ids),
                "threeRoutesValid": valid_routes(move, generated.analysis),
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
                "keyPiecesCorrect": False,
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
    table_rows = []
    for item in results:
        table_rows.append(
            "| {id} | {complexity} | {first} | {retry} | {fallback} | {input} | {output} | "
            "{latency} | {pieces} | {danger} | {plans} | {routes} |".format(
                id=item["id"],
                complexity=item["complexity"],
                first="是" if item["firstPass"] else "否",
                retry="是" if item["retried"] else "否",
                fallback="是" if item["fallback"] else "否",
                input=item.get("inputTokens") or "—",
                output=item.get("outputTokens") or "—",
                latency=item.get("firstLatencyMs") or "—",
                pieces="是" if item["keyPiecesCorrect"] else "否",
                danger="是" if item["dangerHasEvidence"] else "否",
                plans="是" if item["plansHaveEvidence"] else "否",
                routes="是" if item["threeRoutesValid"] else "否",
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
        issue_rows.append("- 15 个局面均无原始输出校验错误。")
    normalization_rows = []
    for item in results:
        for issue in item.get("normalizations", []):
            normalization_rows.append(
                f"- `{item['id']}` attempt {issue.get('attempt', '—')} `{issue['path']}`：{issue['message']}"
            )
    if not normalization_rows:
        normalization_rows.append("- 无需移除事实包外棋盘字面量。")

    return f"""# 专业棋局分析质量与性能报告

生成模型：`{model}`。本报告不包含 API Key、Authorization 请求头或任何密钥内容。

## game1 基线问题

旧版复杂局面输入为 80,620 Token，首次响应 75,136ms；两次原始输出均失败并使用安全回退。失败类型包括：

- 第一次：`candidateLines[*].firstMove / continuationPhases[*].moves` 出现不属于三条 Stockfish 路线的 `Bxh7+`、`Qxc3`、`Qxh2+`；`playedMoveAnalysis.positiveEffects` 把实战走法写成未验证吃子；`weaknesses.white[*].evidenceRefs` 引用了错误一方；`mainDanger` 缺少来源格和目标格，且描述了事实包中不存在的将军；正文 2,259 字，超过复杂局面上限。
- 第二次：多个 `evidenceRefs` 使用不存在的 `centipawnLoss:103` 和 `fact:move-1-after:key:pv_key_piece:black:c7`；候选路线中出现 `Bxe7`、`Bxh7+`、`Qxh2+`、`Rxb7`、`Rxh6`；`keyPieces[*]` 声称存在局面前 FEN 中没有的 `white_bishop@g5`；`weaknesses.white[*].evidenceRefs` 黑白说反；再次描述不存在的将军；正文 2,260 字。
- 安全回退曾把结果 FEN 的一段 `p7` 误判成棋盘格；现已停止把结果 FEN写入正文，只保留结构化结果事实。

## 优化方案

- 棋子改用固定的 `keyPieces.white.pieceRef` / `keyPieces.black.pieceRef`。
- 候选路线只返回 `lineRef`；完整 PV 只返回已有 `plyRefs`，SAN、UCI、格子、棋子与结果局面由后端填充。
- 提示词仅发送一个当前 FEN、去重棋子/事实目录、实战走法和三条最多 10 半回合的路线；不发送 legalMoves、positionAfter、重复 evidence 字典、调试字段或整盘历史。
- 保持严格校验：未知 ID、事实包外格子、路线外 SAN、任何 UCI、黑白颠倒、缺证据结论仍会拒绝。
- 分别记录 DeepSeek 网络、校验和后处理耗时；保留缓存。

## 15 局面结果

| 局面 | 复杂度 | 首次通过 | 重试 | 回退 | 输入Token | 输出Token | 首次耗时ms | 关键棋子正确 | 最大危险有证据 | 双方计划有证据 | 三条路线有效 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(table_rows)}

## 汇总

- 首次校验通过：{first_passes}/{count}（{first_rate:.1f}%）
- 最终严格校验通过：{final_valid}/{count}（{final_rate:.1f}%）
- 安全回退：{fallbacks}/{count}（{fallback_rate:.1f}%）
- 缓存响应：{cache_ms if cache_ms is not None else '未测得'}ms

## 原始输出校验明细

{chr(10).join(issue_rows)}

## 安全字面量归一化

以下项目不会被放行或返回给前端；后端先替换为“该格/该路线着法”，再执行完整严格校验：

{chr(10).join(normalization_rows)}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--ids", nargs="*", default=[])
    args = parser.parse_args()
    asyncio.run(run_suite(args.fixtures, args.results, args.report, set(args.ids) or None))


if __name__ == "__main__":
    main()
