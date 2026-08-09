import { syntheticDashboard, syntheticTask } from "./demoData";
import { STAGES } from "./types";
import type {
  DashboardData,
  DecisionRequest,
  EvidenceItem,
  Experiment,
  ResearchStage,
  ResearchTask,
  TraceEvent,
} from "./types";

const REQUIRED_EVIDENCE = new Set([
  "code",
  "config",
  "dataset_manifest",
  "log",
  "metric",
  "trace",
  "review",
]);

const STAGE_TIMES: Record<ResearchStage, string> = Object.fromEntries(
  STAGES.map((stage, index) => [
    stage,
    new Date(Date.UTC(2026, 7, 9, 12, 42, 8 + index)).toISOString(),
  ]),
) as Record<ResearchStage, string>;

const replayTrace: TraceEvent[] = [
  ...syntheticTask.trace,
  {
    id: "tr-6",
    at: "12:42:11.083",
    agent: "Human Replay",
    kind: "control",
    target: "replay.approval.recorded",
    status: "ok",
    message: "Synthetic browser grant recorded; no signature, API request, MCP call, or GPU action occurred.",
    durationMs: 4,
  },
  {
    id: "tr-7",
    at: "12:42:12.401",
    agent: "Runtime Replay",
    kind: "control",
    target: "replay.execution.started",
    status: "running",
    message: "The bounded experiment fixture entered EXECUTE in browser memory.",
    durationMs: 12,
  },
  {
    id: "tr-8",
    at: "12:42:13.052",
    agent: "Runtime Replay",
    kind: "control",
    target: "replay.observation.attached",
    status: "ok",
    message: "Synthetic telemetry samples were attached; no GPU host is connected.",
    durationMs: 8,
  },
  {
    id: "tr-9",
    at: "12:42:14.229",
    agent: "Evaluator Replay",
    kind: "control",
    target: "replay.metrics.computed",
    status: "ok",
    message: "Deterministic fixture metrics were compared across three fixed seeds.",
    durationMs: 31,
  },
  {
    id: "tr-10",
    at: "12:42:15.114",
    agent: "Reviewer Replay",
    kind: "control",
    target: "replay.evidence.verified",
    status: "ok",
    message: "The independent-review fixture completed; seven required artifacts are present.",
    durationMs: 17,
  },
  {
    id: "tr-11",
    at: "12:42:16.442",
    agent: "Control Replay",
    kind: "control",
    target: "replay.evidence_gate.passed",
    status: "ok",
    message: "The in-browser deterministic gate changed from HOLD to PASS.",
    durationMs: 5,
  },
  {
    id: "tr-12",
    at: "12:42:17.026",
    agent: "Research PI Replay",
    kind: "control",
    target: "replay.decision.keep",
    status: "ok",
    message: "KEEP was restored as the explicitly synthetic fixture decision.",
    durationMs: 6,
  },
  {
    id: "tr-13",
    at: "12:42:18.309",
    agent: "Memory Replay",
    kind: "control",
    target: "replay.archive.completed",
    status: "ok",
    message: "The replay closed with a local-only skill candidate; nothing was published.",
    durationMs: 7,
  },
];

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function stageIndex(stage: ResearchStage): number {
  return STAGES.indexOf(stage);
}

function evidenceFor(stage: ResearchStage): EvidenceItem[] {
  const index = stageIndex(stage);
  return clone(syntheticTask.evidence).map((item) => {
    if (item.kind === "goal") return item;
    if (item.kind === "code" || item.kind === "dataset_manifest") {
      item.status = index >= stageIndex("CONTEXT") ? "verified" : "pending";
    } else if (item.kind === "config") {
      item.status = index >= stageIndex("PLAN") ? "verified" : "pending";
    } else if (item.kind === "log") {
      item.status = index >= stageIndex("OBSERVE") ? "verified" : "pending";
      item.digest = item.status === "verified" ? "sha256:7fc1…8a0d" : undefined;
      item.source = item.status === "verified" ? "browser telemetry fixture" : undefined;
      item.verifiedBy = item.status === "verified" ? "Control Replay" : undefined;
    } else if (item.kind === "metric") {
      item.status = index >= stageIndex("EVALUATE") ? "verified" : "missing";
      item.digest = item.status === "verified" ? "sha256:81d2…1e9c" : undefined;
      item.source = item.status === "verified" ? "deterministic metric fixture" : undefined;
      item.verifiedBy = item.status === "verified" ? "Evaluator Replay" : undefined;
    } else if (item.kind === "trace") {
      item.status = index >= stageIndex("VERIFY") ? "verified" : index >= stageIndex("OBSERVE") ? "present" : "pending";
      item.digest = item.status === "verified" || item.status === "present" ? "sha256:a2f7…c019" : undefined;
      item.source = item.status === "verified" || item.status === "present" ? "browser replay audit" : undefined;
      item.verifiedBy = item.status === "verified" ? "Reviewer Replay" : undefined;
    } else if (item.kind === "review") {
      item.status = index >= stageIndex("VERIFY") ? "verified" : "pending";
      item.digest = item.status === "verified" ? "sha256:04be…d671" : undefined;
      item.source = item.status === "verified" ? "independent-review fixture" : undefined;
      item.verifiedBy = item.status === "verified" ? "Control Replay" : undefined;
    }
    return item;
  });
}

function experimentsFor(stage: ResearchStage): Experiment[] {
  const index = stageIndex(stage);
  const experiments = clone(syntheticTask.experiments);
  if (index >= stageIndex("EXECUTE")) experiments[1].status = "running";
  if (index >= stageIndex("EVALUATE")) {
    Object.assign(experiments[1], {
      status: "passed",
      fps: 12.4,
      mpjpe: 42.4,
      latency: 80.6,
      vram: 7.8,
    });
    Object.assign(experiments[2], {
      status: "failed",
      fps: 9.6,
      mpjpe: 39.9,
      latency: 104.2,
      vram: 6.2,
    });
  }
  return experiments;
}

function traceCountFor(stage: ResearchStage, approvalRecorded: boolean): number {
  const index = stageIndex(stage);
  if (index < stageIndex("APPROVAL")) return Math.max(1, index + 1);
  if (stage === "APPROVAL") return approvalRecorded ? 6 : 5;
  if (stage === "EXECUTE") return 7;
  if (stage === "OBSERVE") return 8;
  if (stage === "EVALUATE") return 9;
  if (stage === "VERIFY") return 10;
  if (stage === "DECIDE") return 12;
  return 13;
}

function taskFor(
  stage: ResearchStage,
  generation: string,
  approvalStatus: "pending" | "approved" | "rejected" = "pending",
): ResearchTask {
  const evidence = evidenceFor(stage);
  const present = evidence.filter(
    (item) => REQUIRED_EVIDENCE.has(item.kind) && (item.status === "present" || item.status === "verified"),
  ).length;
  const gatePassed = stageIndex(stage) >= stageIndex("DECIDE");
  return {
    ...clone(syntheticTask),
    generation,
    stage,
    status: stage === "COMPLETED" ? "COMPLETED" : stage === "APPROVAL" ? "WAITING_FOR_HUMAN" : "RUNNING",
    updatedAt: STAGE_TIMES[stage],
    experiments: experimentsFor(stage),
    evidence,
    evidencePresent: present,
    gateStatus: gatePassed ? "pass" : "not_run",
    decision: gatePassed ? "KEEP" : undefined,
    trace: replayTrace.slice(0, traceCountFor(stage, approvalStatus === "approved")),
    pendingApproval: stageIndex(stage) >= stageIndex("APPROVAL")
      ? { ...clone(syntheticTask.pendingApproval!), status: approvalStatus }
      : undefined,
  };
}

function dashboardFor(task: ResearchTask): DashboardData {
  return {
    ...clone(syntheticDashboard),
    tasks: [task],
    activeTaskId: task.id,
    generatedAt: task.updatedAt,
    runtimeMode: "static_replay",
  };
}

export function createStaticReplayApi() {
  let generationCounter = 1;
  let dashboard = dashboardFor(taskFor("APPROVAL", "fixture-generation-001"));
  let grant: { token: string; generation: string; consumed: boolean } | undefined;

  const activeTask = () => dashboard.tasks[0];
  const replaceTask = (task: ResearchTask) => { dashboard = dashboardFor(task); };
  const requireTask = (id: string) => {
    if (activeTask().id !== id) throw new Error("Synthetic replay task was not found.");
  };
  const consumeApproval = (approvalToken?: string) => {
    if (!grant || grant.generation !== activeTask().generation || grant.consumed || approvalToken !== grant.token) {
      throw new Error("A current synthetic browser grant is required at the R2 checkpoint.");
    }
    grant.consumed = true;
  };
  const move = (stage: ResearchStage, approvalStatus: "pending" | "approved" | "rejected" = "approved") => {
    replaceTask(taskFor(stage, activeTask().generation, approvalStatus));
  };

  return {
    async dashboard(): Promise<DashboardData> {
      return clone(dashboard);
    },

    async task(id: string): Promise<ResearchTask> {
      requireTask(id);
      return clone(activeTask());
    },

    async reset(): Promise<DashboardData> {
      generationCounter += 1;
      grant = undefined;
      replaceTask(taskFor("INTAKE", `fixture-generation-${String(generationCounter).padStart(3, "0")}`));
      return clone(dashboard);
    },

    async advance(id: string, approvalToken?: string): Promise<ResearchTask> {
      requireTask(id);
      const current = activeTask();
      if (current.stage === "COMPLETED") return clone(current);
      if (current.stage === "APPROVAL") consumeApproval(approvalToken);
      const next = STAGES[Math.min(STAGES.length - 1, stageIndex(current.stage) + 1)];
      move(next, next === "APPROVAL" ? "pending" : "approved");
      return clone(activeTask());
    },

    async autorun(id: string, approvalToken?: string): Promise<ResearchTask> {
      requireTask(id);
      const current = activeTask();
      const index = stageIndex(current.stage);
      if (index < stageIndex("APPROVAL")) {
        move("APPROVAL", "pending");
      } else if (current.stage === "APPROVAL") {
        consumeApproval(approvalToken);
        move("VERIFY");
      } else if (index < stageIndex("VERIFY")) {
        move("VERIFY");
      } else if (current.stage === "VERIFY") {
        move("COMPLETED");
      } else if (current.stage !== "COMPLETED") {
        move("COMPLETED");
      }
      return clone(activeTask());
    },

    async decide(approvalId: string, payload: DecisionRequest): Promise<{ approval_token?: string }> {
      const current = activeTask();
      const approval = current.pendingApproval;
      if (current.stage !== "APPROVAL" || !approval || approval.id !== approvalId) {
        throw new Error("The synthetic approval checkpoint is no longer active.");
      }
      if (payload.expected_digest !== approval.expectedDigest) {
        throw new Error("The expected digest does not match the synthetic action fixture.");
      }
      if (payload.decision === "denied") {
        grant = undefined;
        move("APPROVAL", "rejected");
        return {};
      }
      const token = `synthetic_replay_grant:${current.generation}:${approval.expectedDigest}`;
      grant = { token, generation: current.generation, consumed: false };
      move("APPROVAL", "approved");
      return { approval_token: token };
    },
  };
}

export type StaticReplayApi = ReturnType<typeof createStaticReplayApi>;
