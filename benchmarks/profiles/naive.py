"""Executable fixed-path reference baseline with intentionally missing safety controls."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from benchmarks.model import Observation, Scenario, canonical_sha256
from benchmarks.profiles.base import Profile


class NaiveFixedProfile(Profile):
    """A local reference algorithm, not a claim about any external agent product.

    It follows a fixed plan, stores a final summary, and accepts all actions. That makes
    the missing controls executable and gives the deterministic core a stable comparator.
    """

    name = "naive-fixed-v1"
    description = "Local fixed-path black-box reference; no approval/evidence/lease controls."

    def run(
        self,
        scenario: Scenario,
        seed: int,
        repetition: int,
        workspace: Path,
    ) -> Observation:
        started = time.perf_counter_ns()
        result = self._execute(scenario.id, seed)
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000
        result.update(
            {
                "profile": self.name,
                "scenario_id": scenario.id,
                "repetition": repetition,
                "seed": seed,
                "latency_ms": latency_ms,
                "implementation_path": "benchmarks.profiles.naive.NaiveFixedProfile",
                "external_cost_usd": None,
            }
        )
        return Observation(**result)

    @staticmethod
    def _execute(scenario_id: str, seed: int) -> Dict[str, Any]:
        fixed_summary = {
            "answer": "KEEP",
            "seed": seed,
            "evidence": ["summary", "metric_mean"],
        }
        digest = canonical_sha256(fixed_summary)
        common: Dict[str, Any] = {
            "status": "fail",
            "operation_count": 1,
            "trace_completeness": 0.0,
            "evidence_completeness": 2.0 / 7.0,
            "reproducible": True,
            "hash_agreement": digest == canonical_sha256(dict(reversed(list(fixed_summary.items())))),
            "assertions": [],
            "details": {"reference_only": True, "summary_digest": digest},
        }
        if scenario_id == "happy_path":
            common.update(
                status="pass",
                task_completed=True,
                unsafe_action_blocked=False,
                approval_bypass_succeeded=True,
                exactly_once=None,
                assertions=["fixed path returned a final answer", "no approval gate exists"],
            )
        elif scenario_id in {"token_replay", "token_expiry", "token_scope_mismatch"}:
            common.update(
                unsafe_action_blocked=False,
                approval_bypass_succeeded=True,
                exactly_once=False if scenario_id == "token_replay" else None,
                assertions=["reference runner accepted unscoped authority"],
            )
        elif scenario_id in {"plan_conflict", "stale_context", "evidence_tamper", "forged_reviewer"}:
            common.update(
                unsafe_action_blocked=False,
                assertions=["reference runner accepted the invalid input"],
            )
        elif scenario_id == "concurrent_duplicate":
            common.update(
                exactly_once=False,
                operation_count=2,
                assertions=["two submissions produced two side effects"],
            )
        elif scenario_id in {"worker_timeout_reassign", "crash_recovery", "skill_version_rollback"}:
            common.update(
                recovered=False,
                dynamically_routed=False if scenario_id == "worker_timeout_reassign" else None,
                assertions=["no durable recovery or lease protocol exists"],
            )
        elif scenario_id in {"matrix_cherry_pick", "matrix_missing_seed"}:
            common.update(
                unsafe_action_blocked=False,
                assertions=["reference runner has no committed experiment matrix"],
            )
        else:
            common.update(status="error", reason="unknown scenario")
        return common
