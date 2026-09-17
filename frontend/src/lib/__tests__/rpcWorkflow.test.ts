import { describe, expect, it } from "vitest";

import {
  nonEmptyQuoteIndices,
  canRemoveRecruitBranch,
  makeDefaultRpcBranch,
  readinessIssueCount,
  readinessSteps,
  referencedBlankAcceptQuotes,
} from "../rpcWorkflow";

describe("RPC workflow defaults", () => {
  it("retains the final recruit branch", () => {
    expect(canRemoveRecruitBranch(1)).toBe(false);
    expect(canRemoveRecruitBranch(2)).toBe(true);
  });

  it("starts with an always-available Recruit action", () => {
    expect(makeDefaultRpcBranch()).toEqual({
      approach: 4,
      opinion_required: 0,
      required_item: 0,
      fact_must_be_true: null,
      accept_quote: 10,
    });
  });

  it("reports a referenced blank accept quote", () => {
    expect(referencedBlankAcceptQuotes(
      [makeDefaultRpcBranch()],
      ["hello"],
    )).toEqual([10]);
  });

  it("maps engine closure stages to the editor tabs that fix them", () => {
    const steps = readinessSteps({
      profile: 63,
      ready: false,
      stages: {
        profile: { status: "ready", detail: "Type 3" },
        portraits: { status: "blocked", detail: "No talk face" },
        placement: { status: "blocked", detail: "Not placed" },
        recruitment: { status: "ready", detail: "One branch" },
        dialogue: { status: "blocked", detail: "Quote 10 blank" },
        post_recruit: { status: "advisory", detail: "Optional" },
      },
    });
    expect(steps.map((step) => [step.key, step.tab])).toEqual([
      ["profile", "profile"],
      ["portraits", "portrait"],
      ["placement", "placement"],
      ["recruitment", "recruitment"],
      ["dialogue", "recruitment"],
      ["post_recruit", "voice"],
    ]);
    expect(readinessIssueCount(steps)).toBe(3);
  });

  it("shows only populated sparse EDT slots in the compact bark editor", () => {
    const quotes = Array.from({ length: 146 }, () => "");
    quotes[0] = "First bark";
    quotes[27] = "Combat bark";
    quotes[145] = "Last bark";

    expect(nonEmptyQuoteIndices(quotes)).toEqual([0, 27, 145]);
  });
});
