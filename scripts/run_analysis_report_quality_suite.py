from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.analysis_report import build_analysis_report, validate_report_package
from app.chess_facts import build_move_fact_package
from app.config import load_settings
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.narrative_generator import NarrativeGenerator
from app.strategic_plans import StrategicPlanAnalyzer
from app.threat_analysis import ThreatAnalyzer, ThreatPackage, position_id


DEFAULT_FIXTURES = Path("tests/fixtures/professional_validation_positions.json")
DEFAULT_RESULTS = Path("docs/analysis-report-quality-results.json")
DEFAULT_REPORT = Path("docs/analysis-report-quality-report.md")


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
    generator = NarrativeGenerator(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_seconds=settings.deepseek_timeout_seconds,
    )
    threat_analyzer = ThreatAnalyzer()
    plan_analyzer = StrategicPlanAnalyzer()
    results: list[dict[str, Any]] = []

    for index, fixture in enumerate(fixtures, start=1):
        print(f"[{index}/{len(fixtures)}] {fixture['id']} starting", flush=True)
        row: dict[str, Any] = {
            "id": fixture["id"],
            "category": fixture["category"],
            "expectedComplexity": fixture["complexity"],
        }
        started = time.perf_counter()
        try:
            review = await analyze_pgn(
                pgn=fixture["pgn"],
                stockfish=engine,
                analysis_id=f"analysis-report-quality-{fixture['id']}",
                depth=10,
                timeout_seconds=90,
                max_plies=2,
            )
            move = review.moves[0]

            assembly_started = time.perf_counter()
            facts = build_move_fact_package(move)
            threats = threat_analyzer.detect(facts)
            threat_package = ThreatPackage(
                position_id=position_id(facts.position.fen),
                threats=threats,
            )
            facts.threats = threats
            plans = plan_analyzer.analyze(
                facts,
                position_facts=move.position_facts,
                threat_package=threat_package,
            )
            facts.plans = plans.plans
            package = build_analysis_report(move, facts, threat_package, plans)
            assembly_ms = round((time.perf_counter() - assembly_started) * 1000, 2)

            prompt_text = json.dumps(package.prompt_payload(), ensure_ascii=False)
            generated = await generator.generate(package)
            final_errors = validate_report_package(generated.report)
            row.update({
                "finalValid": not final_errors,
                "fallback": generated.usage.used_fallback,
                "attempts": generated.usage.attempts,
                "singleCallCompliant": generated.usage.attempts <= 1,
                "inputTokens": generated.usage.prompt_tokens,
                "outputTokens": generated.usage.completion_tokens,
                "totalTokens": generated.usage.total_tokens,
                "narrativeLatencyMs": generated.usage.elapsed_ms,
                "assemblyMs": assembly_ms,
                "totalMs": round((time.perf_counter() - started) * 1000),
                "threatCount": len(generated.report.threat_section.items),
                "planCount": len(generated.report.strategy_section.items),
                "routeCount": len(generated.report.route_section.routes),
                "summaryRefs": generated.report.summary_section.source_refs,
                "fenExcluded": move.before_fen not in prompt_text,
                "routeMovesExcluded": (
                    "moves_san" not in prompt_text
                    and "moves_uci" not in prompt_text
                ),
                "finalErrors": final_errors,
                "validationWarnings": generated.validation_warnings,
                "report": generated.report.model_dump(),
            })
        except Exception as exc:
            row.update({
                "finalValid": False,
                "fallback": False,
                "attempts": 0,
                "singleCallCompliant": False,
                "totalMs": round((time.perf_counter() - started) * 1000),
                "finalErrors": [f"{type(exc).__name__}: {exc}"],
            })
        results.append(row)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"[{index}/{len(fixtures)}] {fixture['id']} "
            f"valid={row['finalValid']} fallback={row['fallback']} "
            f"tokens={row.get('inputTokens')} narrative_ms={row.get('narrativeLatencyMs')}",
            flush=True,
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        render_report(results, settings.deepseek_model),
        encoding="utf-8",
    )
    print(f"report={report_path}", flush=True)
    return results


def render_report(results: list[dict[str, Any]], model: str) -> str:
    count = len(results)
    valid = sum(bool(item.get("finalValid")) for item in results)
    fallbacks = sum(bool(item.get("fallback")) for item in results)
    single_call = sum(bool(item.get("singleCallCompliant")) for item in results)
    assembly = [
        float(item["assemblyMs"])
        for item in results
        if isinstance(item.get("assemblyMs"), (int, float))
    ]
    narrative = [
        int(item["narrativeLatencyMs"])
        for item in results
        if isinstance(item.get("narrativeLatencyMs"), int)
    ]
    rows = "\n".join(
        "| {id} | {valid} | {fallback} | {attempts} | {tokens} | {assembly} | {latency} |".format(
            id=item["id"],
            valid="是" if item.get("finalValid") else "否",
            fallback="是" if item.get("fallback") else "否",
            attempts=item.get("attempts"),
            tokens=item.get("totalTokens"),
            assembly=item.get("assemblyMs"),
            latency=item.get("narrativeLatencyMs"),
        )
        for item in results
    )
    samples = []
    for item in results[:3]:
        report = item.get("report") or {}
        samples.append(
            "### {id}\n\n- 局面：{position}\n- 本步：{move}\n- 总结：{summary}".format(
                id=item["id"],
                position=(report.get("position_overview") or {}).get("text", ""),
                move=(report.get("move_analysis") or {}).get("text", ""),
                summary=(report.get("summary_section") or {}).get("text", ""),
            )
        )
    return f"""# Analysis Report Phase 4 质量报告

- 模型：`{model}`
- 局面数：{count}
- 最终校验通过：{valid}/{count}
- 使用安全回退：{fallbacks}/{count}
- 单次调用约束通过：{single_call}/{count}
- 程序编排耗时中位数：{statistics.median(assembly) if assembly else '无'} ms
- Narrative 网络耗时中位数：{statistics.median(narrative) if narrative else '无'} ms

| 局面 | 最终有效 | 回退 | 调用次数 | 总Token | 编排ms | Narrative ms |
|---|---:|---:|---:|---:|---:|---:|
{rows}

## 代表性最终文本

{chr(10).join(samples)}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--ids", nargs="*", default=[])
    args = parser.parse_args()
    asyncio.run(
        run_suite(
            args.fixtures,
            args.results,
            args.report,
            set(args.ids) or None,
        )
    )


if __name__ == "__main__":
    main()
