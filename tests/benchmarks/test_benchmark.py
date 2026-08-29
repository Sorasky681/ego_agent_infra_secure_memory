import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.model import canonical_json, canonical_sha256, derive_seed, load_corpus
from benchmarks.profiles import AgentTeamsRXPProfile, DeterministicCoreProfile, NaiveFixedProfile
from benchmarks.report import render_markdown
from benchmarks.runner import run_benchmark, strict_failures


def test_corpus_is_versioned_unique_and_seeded() -> None:
    corpus = load_corpus()
    assert corpus.benchmark == "rxp-bench/v1"
    assert corpus.corpus_version == "1.0.0"
    assert len(corpus.scenarios) == 14
    assert len({scenario.id for scenario in corpus.scenarios}) == 14
    assert derive_seed(corpus.master_seed, "happy_path", 0) == derive_seed(
        corpus.master_seed, "happy_path", 0
    )
    assert derive_seed(corpus.master_seed, "happy_path", 0) != derive_seed(
        corpus.master_seed, "happy_path", 1
    )


def test_profiles_match_golden_control_outcomes() -> None:
    result = run_benchmark([NaiveFixedProfile(), DeterministicCoreProfile()], 1, 20260829)
    actual = {
        profile: {
            scenario: next(status for status, count in counts.items() if count == 1)
            for scenario, counts in summary["scenario_status"].items()
        }
        for profile, summary in result["summary"]["profiles"].items()
    }
    golden = json.loads(
        (
            Path(__file__).parent / "golden" / "expected-status-v1.json"
        ).read_text(encoding="utf-8")
    )
    assert actual == golden
    assert strict_failures(result) == []


def test_deterministic_core_has_zero_approval_bypass() -> None:
    result = run_benchmark([DeterministicCoreProfile()], 2, 20260829)
    core = result["summary"]["profiles"]["deterministic-core-v0.1"]
    assert core["approval_bypass_success"]["successes"] == 0
    assert core["approval_bypass_success"]["n"] == 8
    assert core["exactly_once"]["value"] == 1.0
    assert core["scenario_success"]["value"] == 1.0
    assert core["coverage"] == 10 / 14


def test_canonical_output_and_markdown_report() -> None:
    result = run_benchmark([NaiveFixedProfile()], 1, 7)
    encoded = canonical_json(result)
    assert canonical_json(json.loads(encoded)) == encoded
    assert len(canonical_sha256(result)) == 64
    report = render_markdown(result)
    assert "RXP Bench report" in report
    assert "Approval bypass" in report
    assert "not measured" in report
    assert result["semantic_digest"] in report


def test_agentteams_pass_requires_three_roles_and_digest_bound_trace(tmp_path: Path) -> None:
    trace = tmp_path / "agentteams-trace.json"
    trace.write_text('{"source":"agentteams","events":[]}', encoding="utf-8")
    details = {
        "execution_mode": "real-agentteams",
        "agent_roles": ["pi", "runtime", "reviewer"],
        "agentteams_trace_path": trace.name,
        "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
    }
    AgentTeamsRXPProfile._validate_pass_evidence(
        {"status": "pass", "details": details}, tmp_path
    )

    with pytest.raises(ValueError, match="three distinct"):
        AgentTeamsRXPProfile._validate_pass_evidence(
            {"status": "pass", "details": {**details, "agent_roles": ["pi", "pi"]}},
            tmp_path,
        )
    with pytest.raises(ValueError, match="digest"):
        AgentTeamsRXPProfile._validate_pass_evidence(
            {"status": "pass", "details": {**details, "trace_sha256": "0" * 64}},
            tmp_path,
        )
