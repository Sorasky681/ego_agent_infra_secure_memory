import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { approvalTokenForGeneration, EvidenceLedger, RXPProtocolView, StageSpine } from "./App";
import { syntheticTask } from "./demoData";
import { syntheticRXP } from "./rxpDemoData";

vi.mock("framer-motion", async () => {
  const React = await import("react");
  const passthrough = React.forwardRef<HTMLElement, React.HTMLAttributes<HTMLElement> & { children?: React.ReactNode }>(
    ({ children, ...props }, ref) => React.createElement("div", { ...props, ref }, children),
  );
  return {
    AnimatePresence: ({ children }: { children: React.ReactNode }) => children,
    motion: new Proxy({}, { get: () => passthrough }),
    useReducedMotion: () => true,
  };
});

describe("ResearchOps cockpit primitives", () => {
  it("marks the current deterministic workflow stage", () => {
    render(<StageSpine current="APPROVAL" reducedMotion />);

    expect(screen.getByText("APPROVAL").closest(".stage-node")).toHaveAttribute("aria-current", "step");
    expect(screen.getByLabelText(/workflow stage approval/i)).toBeInTheDocument();
  });

  it("opens an evidence item through the ledger affordance", () => {
    const onInspect = vi.fn();
    render(
      <EvidenceLedger
        evidence={syntheticTask.evidence}
        present={syntheticTask.evidencePresent}
        required={syntheticTask.evidenceRequired}
        gateStatus={syntheticTask.gateStatus}
        onInspect={onInspect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /inspect frozen researchgoal/i }));
    expect(onInspect).toHaveBeenCalledWith(syntheticTask.evidence[0]);
    expect(screen.getByText("3 / 7 artifacts present")).toBeInTheDocument();
    expect(screen.getByText("HOLD")).toBeInTheDocument();
  });

  it("never reuses a one-time approval token across task generations", () => {
    const grant = { token: "one-time-token", generation: "gen-old" };
    expect(approvalTokenForGeneration(grant, "gen-old")).toBe("one-time-token");
    expect(approvalTokenForGeneration(grant, "gen-new")).toBeUndefined();
  });

  it("shows RXP matrix completeness and lets a judge inspect a committed cell", () => {
    render(<RXPProtocolView data={syntheticRXP} runtimeMode="static_replay" />);

    expect(screen.getByText("2/2 COMPLETE")).toBeInTheDocument();
    expect(screen.getByText(/verifier not executed here/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cell-candidate/i }));
    expect(screen.getByTitle(syntheticRXP.cells[1].intentDigest)).toHaveTextContent(/sha256:/);
  });
});
