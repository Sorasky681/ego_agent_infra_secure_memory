"""Truthful adapter boundary for the future AgentTeams + RXP implementation."""

from __future__ import annotations

import hashlib
import importlib.util
import time
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional, Tuple

from benchmarks import BENCHMARK_VERSION
from benchmarks.model import Observation, Scenario
from benchmarks.profiles.base import Profile


class AgentTeamsRXPProfile(Profile):
    name = "agentteams-rxp-target"
    description = "Target adapter; executes only when a version-matched real adapter is present."

    @staticmethod
    def _validate_pass_evidence(raw: Dict[str, Any], workspace: Path) -> None:
        if raw.get("status") != "pass":
            return
        details = raw.get("details")
        if not isinstance(details, dict):
            raise ValueError("PASS requires a details object with AgentTeams trace evidence")
        if details.get("execution_mode") != "real-agentteams":
            raise ValueError("PASS requires details.execution_mode='real-agentteams'")
        roles = details.get("agent_roles")
        if not isinstance(roles, list) or len(set(roles)) < 3:
            raise ValueError("PASS requires at least three distinct AgentTeams roles")
        trace_value = details.get("agentteams_trace_path")
        expected_digest = details.get("trace_sha256")
        if not isinstance(trace_value, str) or not isinstance(expected_digest, str):
            raise ValueError("PASS requires agentteams_trace_path and trace_sha256")
        trace_path = (workspace / trace_value).resolve()
        workspace_root = workspace.resolve()
        if workspace_root not in trace_path.parents:
            raise ValueError("AgentTeams trace must be written inside the trial workspace")
        if not trace_path.is_file():
            raise ValueError("AgentTeams trace artifact does not exist")
        actual_digest = hashlib.sha256(trace_path.read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError("AgentTeams trace digest does not match the artifact")

    @staticmethod
    def _load_adapter() -> Tuple[Optional[ModuleType], str]:
        adapter_path = Path.cwd() / "integrations" / "agentteams" / "benchmark_adapter.py"
        if not adapter_path.is_file():
            return None, "integrations/agentteams/benchmark_adapter.py is not implemented"
        spec = importlib.util.spec_from_file_location("egoagentos_agentteams_benchmark", adapter_path)
        if spec is None or spec.loader is None:
            return None, "AgentTeams benchmark adapter cannot be loaded"
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as error:
            return None, "AgentTeams benchmark adapter import failed: %s" % type(error).__name__
        version = getattr(module, "BENCHMARK_ADAPTER_VERSION", None)
        if version != BENCHMARK_VERSION:
            return None, "adapter version %r does not match %s" % (version, BENCHMARK_VERSION)
        if not callable(getattr(module, "run_scenario", None)):
            return None, "adapter does not export callable run_scenario"
        return module, ""

    def run(
        self,
        scenario: Scenario,
        seed: int,
        repetition: int,
        workspace: Path,
    ) -> Observation:
        module, reason = self._load_adapter()
        if module is None:
            return Observation.skipped(
                self.name,
                scenario,
                repetition,
                seed,
                reason,
                "integrations.agentteams.benchmark_adapter.run_scenario",
            )
        started = time.perf_counter_ns()
        try:
            raw: Dict[str, Any] = module.run_scenario(asdict(scenario), seed, workspace)
            if not isinstance(raw, dict):
                raise TypeError("run_scenario must return a dict")
            if raw.get("status") not in {"pass", "fail", "skip"}:
                raise ValueError("run_scenario status must be pass, fail, or skip")
            self._validate_pass_evidence(raw, workspace)
            protected = {
                "profile": self.name,
                "scenario_id": scenario.id,
                "repetition": repetition,
                "seed": seed,
                "latency_ms": (time.perf_counter_ns() - started) / 1_000_000,
                "implementation_path": "integrations.agentteams.benchmark_adapter.run_scenario",
            }
            raw.update(protected)
            raw.setdefault("operation_count", 0)
            raw.setdefault("external_cost_usd", None)
            raw.setdefault("assertions", [])
            raw.setdefault("details", {})
            return Observation(**raw)
        except Exception as error:
            return Observation(
                profile=self.name,
                scenario_id=scenario.id,
                repetition=repetition,
                seed=seed,
                status="error",
                latency_ms=(time.perf_counter_ns() - started) / 1_000_000,
                operation_count=0,
                reason="%s: %s" % (type(error).__name__, str(error)),
                assertions=["real target adapter raised an exception"],
                implementation_path="integrations.agentteams.benchmark_adapter.run_scenario",
            )
