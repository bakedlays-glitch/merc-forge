import type { RpcBranch, RpcReadiness } from "./api";

export function makeDefaultRpcBranch(): RpcBranch {
  return {
    approach: 4,
    opinion_required: 0,
    required_item: 0,
    fact_must_be_true: null,
    accept_quote: 10,
  };
}

/** Recruitment dialogue must retain at least one engine-valid branch. */
export function canRemoveRecruitBranch(branchCount: number): boolean {
  return branchCount > 1;
}

export function referencedBlankAcceptQuotes(branches: RpcBranch[], quotes: string[]): number[] {
  return [...new Set(
    branches
      .map((branch) => branch.accept_quote)
      .filter((quote) => !quotes[quote]?.trim()),
  )].sort((a, b) => a - b);
}

/** Keep sparse EDT banks compact in the editor without changing slot numbers. */
export function nonEmptyQuoteIndices(quotes: string[]): number[] {
  return quotes.flatMap((quote, index) => quote.trim() ? [index] : []);
}

const STEP_META = [
  ["profile", "Profile", "profile"],
  ["portraits", "Two face systems", "portrait"],
  ["placement", "Map position", "placement"],
  ["recruitment", "Recruit action", "recruitment"],
  ["dialogue", "Recruit dialogue", "recruitment"],
  ["post_recruit", "After recruitment", "voice"],
] as const;

export function readinessSteps(readiness: RpcReadiness) {
  return STEP_META.map(([key, label, tab]) => ({
    key,
    label,
    tab,
    status: readiness.stages[key]?.status ?? "blocked",
    detail: readiness.stages[key]?.detail ?? "Not checked.",
  }));
}

export function readinessIssueCount(steps: ReturnType<typeof readinessSteps>): number {
  return steps.filter((step) => step.status === "blocked").length;
}
