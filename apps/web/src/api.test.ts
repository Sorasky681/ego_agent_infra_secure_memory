import { afterEach, describe, expect, it, vi } from "vitest";
import { createResearchApi, normalizeDashboard, normalizeRXP, taskEventStreamUrl } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("backend contract normalization", () => {
  it("builds a same-origin event stream URL with an encoded task id", () => {
    expect(taskEventStreamUrl("task/a b")).toBe("/api/v1/tasks/task%2Fa%20b/event-stream");
  });

  it("maps the deterministic API envelope without turning configured metadata into a live claim", () => {
    const dashboard = normalizeDashboard({
      demo: { task_id: "ego-lite-001", synthetic: true },
      tasks: [
        {
          id: "ego-lite-001",
          generation: "gen-current",
          title: "EgoLite Streaming Perception",
          objective: "Bounded synthetic experiment",
          stage: "MEMORY_SKILL",
          status: "running",
          risk_level: "R2",
          goal: {
            acceptance_metrics: [
              {
                name: "MPJPE",
                direction: "lower_better",
                threshold: 0.05,
                unit: "millimetres",
                rule: "relative candidate degradation <= 5%",
              },
            ],
            candidate_arms: [{ id: "baseline", name: "Baseline", description: "Frozen" }],
          },
          evidence_summary: { required: ["code", "metric"], present: ["code"] },
          gate_result: { status: "not_run" },
          decision: "KEEP",
        },
      ],
      activity: [
        {
          id: "event-1",
          actor: "reviewer-agent",
          event_type: "evidence.gate.passed",
          created_at: "2026-08-09T10:00:00Z",
        },
      ],
      integrations: {
        items: [
          {
            id: "nacos",
            name: "Nacos Skill Registry",
            role: "skill registry adapter",
            status: "configured_unverified",
            detail: "Configured but no live handshake was verified.",
          },
        ],
      },
    });

    expect(dashboard.activeTaskId).toBe("ego-lite-001");
    expect(dashboard.tasks[0].stage).toBe("MEMORY_SKILL");
    expect(dashboard.tasks[0].generation).toBe("gen-current");
    expect(dashboard.tasks[0].gateStatus).toBe("not_run");
    expect(dashboard.tasks[0].decision).toBe("KEEP");
    expect(dashboard.tasks[0].acceptance[0]).toMatchObject({ target: 5, unit: "%" });
    expect(dashboard.tasks[0].evidenceRequired).toBe(2);
    expect(dashboard.tasks[0].trace[0].target).toBe("evidence.gate.passed");
    expect(dashboard.integrations[0].status).toBe("unconfigured");
    expect(dashboard.runtimeMode).toBe("local_api");
  });

  it("automatically enters static replay when no backend can be reached", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network unavailable"));
    vi.stubGlobal("fetch", fetchMock);
    const api = createResearchApi(false);

    const dashboard = await api.dashboard();
    expect(dashboard.runtimeMode).toBe("static_replay");
    expect(dashboard.tasks[0].stage).toBe("APPROVAL");

    await api.dashboard();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const rxp = await api.rxpDemo();
    expect(rxp.root).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(rxp.productionSignatureTrust).toBe(false);
  });

  it("normalizes the RXP causal ledger without inventing production trust", () => {
    const rxp = normalizeRXP({
      protocol: "RXP/1.0",
      execution_class: "synthetic deterministic fixture",
      physical_gpu_run: false,
      production_signature_trust: false,
      fixture_signature_verified: true,
      structural_verification: "PASS",
      canonical_sha256: "sha256:fixture",
      ledger: {
        matrix_id: "matrix:test",
        completeness: "COMPLETE",
        expected_cell_count: 1,
        decided_cell_count: 1,
        missing_decisions: [],
        entry_count: 12,
        root: "sha256:root",
        cells: [
          {
            cell_id: "cell-a",
            state: "DECIDED",
            determinism_level: "D2_SEEDED_ENV_BOUND",
            intent_digest: "sha256:intent",
            evidence_digests: ["sha256:evidence"],
          },
        ],
      },
    });

    expect(rxp.completeness).toBe("COMPLETE");
    expect(rxp.cells[0]).toMatchObject({ cellId: "cell-a", evidenceCount: 1 });
    expect(rxp.productionSignatureTrust).toBe(false);
    expect(rxp.physicalGpuRun).toBe(false);
  });
});
