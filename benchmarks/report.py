"""Markdown rendering for benchmark JSON."""

from __future__ import annotations

from typing import Any, Dict, List


def _pct(metric: Dict[str, Any]) -> str:
    value = metric.get("value")
    if value is None:
        return "N/A"
    ci = metric.get("ci95")
    if ci:
        return "%.1f%% [%.1f, %.1f]" % (value * 100, ci[0] * 100, ci[1] * 100)
    return "%.1f%%" % (value * 100)


def _number(value: Any, suffix: str = "") -> str:
    return "N/A" if value is None else "%.3f%s" % (value, suffix)


def render_markdown(result: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# RXP Bench report",
        "",
        "> Synthetic, local, non-GPU infrastructure benchmark. It does not report model quality "
        "or physical experiment performance.",
        "",
        "- Benchmark: `%s`" % result["benchmark"],
        "- Corpus: `%s` (`%s`)" % (result["corpus_version"], result["corpus_digest"]),
        "- Seed: `%s`; repetitions: `%s`" % (
            result["configuration"]["master_seed"],
            result["configuration"]["repetitions"],
        ),
        "- Generated: `%s`" % result["generated_at"],
        "- Environment: `%s`, Python `%s`, GPU `%s`" % (
            result["environment"]["platform"],
            result["environment"]["python"],
            result["environment"]["gpu"],
        ),
        "- Semantic result digest: `%s`" % result["semantic_digest"],
        "",
        "## Profile comparison",
        "",
        "| Profile | Coverage | Scenario success (95% CI) | Unsafe block | Approval bypass | Exactly once | Recovery | Dynamic routing | Latency p50 / p95 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for profile_name, summary in result["summary"]["profiles"].items():
        latency = summary["latency_ms"]
        lines.append(
            "| `%s` | %.1f%% | %s | %s | %s | %s | %s | %s | %s / %s ms |"
            % (
                profile_name,
                summary["coverage"] * 100,
                _pct(summary["scenario_success"]),
                _pct(summary["unsafe_action_block"]),
                _pct(summary["approval_bypass_success"]),
                _pct(summary["exactly_once"]),
                _pct(summary["recovery"]),
                _pct(summary["dynamic_routing"]),
                _number(latency["median"]),
                _number(latency["p95"]),
            )
        )

    lines.extend(
        [
            "",
            "Approval bypass is a failure metric: its required value is **0%**. N/A means that "
            "the profile did not execute a scenario exposing that metric.",
            "",
            "## Scenario outcomes",
            "",
            "| Scenario | " + " | ".join("`%s`" % name for name in result["summary"]["profiles"]) + " |",
            "|---|" + "---|" * len(result["summary"]["profiles"]),
        ]
    )
    scenario_ids = [scenario["id"] for scenario in result["scenarios"]]
    for scenario_id in scenario_ids:
        cells = []
        for summary in result["summary"]["profiles"].values():
            counts = summary["scenario_status"][scenario_id]
            cell = ", ".join("%s %d" % (key.upper(), value) for key, value in counts.items() if value)
            cells.append(cell or "N/A")
        lines.append("| `%s` | %s |" % (scenario_id, " | ".join(cells)))

    lines.extend(
        [
            "",
            "## Evidence and recovery",
            "",
            "| Profile | Trace completeness mean | Evidence completeness mean | Recovery MTTR p50 / p95 | Operations mean | External cost |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for profile_name, summary in result["summary"]["profiles"].items():
        cost = summary["external_cost_usd"]
        cost_text = (
            "$%.6f" % cost["total"] if cost["status"] == "measured" else "not measured"
        )
        lines.append(
            "| `%s` | %s | %s | %s / %s ms | %s | %s |"
            % (
                profile_name,
                _number(summary["trace_completeness"]["mean"]),
                _number(summary["evidence_completeness"]["mean"]),
                _number(summary["mttr_ms"]["median"]),
                _number(summary["mttr_ms"]["p95"]),
                _number(summary["operation_count"]["mean"]),
                cost_text,
            )
        )

    lines.extend(
        [
            "",
            "## Confidence and limitations",
            "",
            "- Binary rates use Wilson 95% confidence intervals.",
            "- Continuous means use 2,000 fixed-seed nonparametric bootstrap resamples; p50 and "
            "p95 are empirical quantiles.",
            "- Wall-clock latency and recovery MTTR are measured locally and include SQLite and "
            "Python overhead. No external billing meter was attached, so monetary cost is null.",
            "- `naive-fixed-v1` is an executable local reference algorithm, not a measurement of "
            "any named vendor or general-purpose agent.",
            "- `agentteams-rxp-target` is SKIP unless the real, version-matched integration adapter "
            "exists. A SKIP is never counted as a pass.",
            "- Synthetic infrastructure tests establish control behavior, not scientific validity "
            "or GPU/model performance.",
            "",
        ]
    )
    return "\n".join(lines)
