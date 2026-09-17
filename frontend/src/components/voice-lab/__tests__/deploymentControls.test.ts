import { describe, expect, it } from "vitest";

import { deploymentActionLabel, shortenHash } from "../DeployDialog";
import { undoAvailability } from "../HistoryPanel";

describe("deployment review formatting", () => {
  it("keeps every destructive action explicit and hashes short", () => {
    expect(deploymentActionLabel("remove_shadowing_variant")).toBe("Remove shadowing variant");
    expect(deploymentActionLabel("create")).toBe("Create");
    expect(shortenHash("a".repeat(64))).toBe("a".repeat(12));
  });
});

describe("Undo safety", () => {
  it("routes conflicts and recovery-required state to recovery review", () => {
    expect(undoAvailability({ status: "deployed", recoveryRequired: false })).toEqual({ canUndo: true, needsRecoveryReview: false });
    expect(undoAvailability({ status: "UNDO_CONFLICT", recoveryRequired: false })).toEqual({ canUndo: false, needsRecoveryReview: true });
    expect(undoAvailability({ status: "deployed", recoveryRequired: true })).toEqual({ canUndo: false, needsRecoveryReview: true });
  });
});
