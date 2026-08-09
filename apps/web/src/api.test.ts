import { afterEach, describe, expect, it, vi } from "vitest";
import { createResearchApi, normalizeDashboard } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("backend contract normalization", () => {
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
  });
});
