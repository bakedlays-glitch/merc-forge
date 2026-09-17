import type {
  EditRecipeDraft,
  TimeRange,
  VoiceBank,
  VoiceFinding,
  VoiceFindingState,
  VoiceLine,
  VoiceSource,
} from "./voiceLabSchema";

export function mergeVoiceBankCatalog(
  catalog: readonly VoiceBank[],
  indexed: readonly VoiceBank[],
): VoiceBank[] {
  const indexedByVoice = new Map(indexed.map((bank) => [bank.voiceIndex, bank]));
  const merged = catalog.map((bank) => indexedByVoice.get(bank.voiceIndex) ?? bank);
  const catalogIndexes = new Set(catalog.map((bank) => bank.voiceIndex));
  return merged.concat(indexed.filter((bank) => !catalogIndexes.has(bank.voiceIndex)));
}

export interface VoiceFindingFilter {
  bank?: number;
  states: VoiceFindingState[];
}

export interface VoiceLabSelection {
  bank: number | null;
  line: string | null;
}

const severityRank = {
  critical: 0,
  high: 1,
  warning: 2,
  medium: 3,
  low: 4,
  info: 5,
} as const;

const stateRank: Record<VoiceFindingState, number> = {
  needs_review: 0,
  deferred: 1,
  accepted: 2,
  intentional: 3,
  false_positive: 4,
  fixed: 5,
};

export function filterAndSortFindings(
  findings: readonly VoiceFinding[],
  filter: VoiceFindingFilter,
): VoiceFinding[] {
  const selectedStates = new Set(filter.states);
  return findings
    .filter((finding) => selectedStates.has(finding.state))
    .filter((finding) => filter.bank === undefined || finding.voiceIndex === filter.bank)
    .slice()
    .sort((left, right) => (
      severityRank[left.severity] - severityRank[right.severity]
      || stateRank[left.state] - stateRank[right.state]
      || right.confidence - left.confidence
      || left.stableKey.localeCompare(right.stableKey)
    ));
}

export function voiceLineKey(family: string, lineId: string): string {
  return `${family}:${lineId}`;
}

export function normalizeVoiceLabSelection(
  selection: VoiceLabSelection,
  banks: readonly { voiceIndex: number; lines: readonly Pick<VoiceLine, "family" | "lineId">[] }[],
): VoiceLabSelection {
  if (selection.bank === null) return { bank: null, line: null };
  const requestedBank = banks.find((candidate) => candidate.voiceIndex === selection.bank) ?? null;
  const bank = requestedBank ?? banks[0] ?? null;
  if (!bank) return { bank: null, line: null };
  const availableLines = bank.lines.map((line) => voiceLineKey(line.family, line.lineId));
  return {
    bank: bank.voiceIndex,
    line: requestedBank && selection.line && availableLines.includes(selection.line)
      ? selection.line
      : null,
  };
}

const battleDescriptions: Record<string, { title: string; explanation: string }> = {
  ATTN: {
    title: "Attention call",
    explanation: "Played as an attention or alert reaction in combat.",
  },
  COOL: {
    title: "Impressed reaction",
    explanation: "Played after a noteworthy combat result.",
  },
  CURSE: {
    title: "Frustrated reaction",
    explanation: "Played as a curse or frustrated combat reaction.",
  },
  DYING: { title: "Dying", explanation: "Played when the character dies." },
  ENEMY: { title: "Enemy spotted", explanation: "Played when an enemy is noticed." },
  HIT1: { title: "Hit reaction 1", explanation: "One of the reactions played when hurt." },
  HIT2: { title: "Hit reaction 2", explanation: "One of the reactions played when hurt." },
  GOTIT: { title: "Acknowledgement", explanation: "Played when the character confirms an order." },
  LAUGH: { title: "Laugh", explanation: "A short laughing reaction." },
  LMATTN: { title: "Low-morale attention call", explanation: "An attention reaction used while morale is low." },
  LMOK1: { title: "Low-morale acknowledgement 1", explanation: "One of the order confirmations used while morale is low." },
  LMOK2: { title: "Low-morale acknowledgement 2", explanation: "One of the order confirmations used while morale is low." },
  LOCKED: { title: "Locked-object reaction", explanation: "Played when an attempted door or object is locked." },
  NOTH: { title: "Nothing found", explanation: "Played when an action finds nothing useful." },
  OK1: { title: "Acknowledgement 1", explanation: "One of the character's order confirmations." },
  OK2: { title: "Acknowledgement 2", explanation: "One of the character's order confirmations." },
  OK3: { title: "Acknowledgement 3", explanation: "One of the character's order confirmations." },
};

export interface VoiceLineDescription {
  category: string;
  title: string;
  technicalId: string;
  explanation: string;
}

function sentenceCase(value: string): string {
  const trimmed = value.trim().replace(/\s+/g, " ");
  return trimmed ? trimmed.charAt(0).toUpperCase() + trimmed.slice(1) : trimmed;
}

function triggerPresentation(meaning: string): { title: string; explanation: string } {
  const hiring = meaning.match(/^AIM hiring screen:\s*(.+)$/i);
  if (hiring) {
    const action = hiring[1]!
      .replace(/^ask (?:the )?player what (.+?) they want$/i, "ask what $1 the player wants")
      .replace(/^ask (?:the )?player what\s+/i, "ask what ")
      .trim();
    return {
      title: sentenceCase(action),
      explanation: `Used during a hiring call to ${action}.`,
    };
  }
  const plain = meaning
    .replace(/^used when\s+/i, "")
    .replace(/^AIM\/MERC:\s*/i, "Hiring or recruitment call: ")
    .replace(/^AIM:\s*/i, "Hiring call: ")
    .replace(/^MERC(?:\/NPC)?:\s*/i, "Recruitment conversation: ")
    .replace(/^JA2UB:\s*/i, "Unfinished Business: ");
  const title = sentenceCase(plain);
  return { title, explanation: `${title.replace(/[.!?]+$/, "")}.` };
}

export function describeVoiceLine(line: VoiceLine): VoiceLineDescription {
  const knownReaction = battleDescriptions[line.lineId.toUpperCase()];
  if (line.family === "battle" || knownReaction) {
    const known = knownReaction;
    return {
      category: line.family === "battle" ? "Combat reaction" : "Voice reaction",
      title: known?.title ?? line.lineId.replaceAll("_", " ").toLowerCase()
        .replace(/^./, (character) => character.toUpperCase()),
      technicalId: `battle / ${line.lineId}`,
      explanation: known?.explanation
        ?? "The game uses this identifier for a combat sound; no confirmed description is recorded.",
    };
  }
  if (line.family === "dialogue_edt") {
    return {
      category: "Subtitle file",
      title: "Dialogue subtitles",
      technicalId: `dialogue_edt / ${line.lineId}`,
      explanation: "The text records shown alongside this merc's spoken lines.",
    };
  }
  const meanings = Object.values(line.triggerByProfileId);
  const meaning = meanings.find((item) => item.meaning.trim())?.meaning;
  const presented = meaning ? triggerPresentation(meaning) : null;
  return {
    category: "Conversation line",
    title: presented?.title ?? `Spoken line ${line.lineId}`,
    technicalId: `speech / ${line.lineId}`,
    explanation: presented?.explanation
      ?? "A numbered spoken line. Its exact in-game trigger is not documented for this profile.",
  };
}

export function filterVoiceLines(lines: readonly VoiceLine[], query: string): VoiceLine[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [...lines];
  return lines.filter((line) => {
    const description = describeVoiceLine(line);
    const triggerText = Object.values(line.triggerByProfileId)
      .flatMap((trigger) => [trigger.name, trigger.meaning]).join(" ");
    const haystack = [line.family, line.lineId, description.category, description.title,
      description.explanation, description.technicalId, triggerText].join(" ").toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}

export function findHighestRiskLine(
  findings: readonly VoiceFinding[],
  voiceIndex: number,
): string | null {
  const best = filterAndSortFindings(findings, { bank: voiceIndex, states: ["needs_review"] })
    .find((finding) => finding.family !== null && finding.lineId !== null);
  return best?.family && best.lineId ? voiceLineKey(best.family, best.lineId) : null;
}

export interface VoiceDraftState {
  source: VoiceSource;
  cuts: readonly TimeRange[];
  subtitle: string | null;
}

export function hasVoiceDraftChanges(
  line: VoiceLine,
  liveSubtitle: string | null,
  draft: VoiceDraftState,
): boolean {
  return draft.source.assetId !== line.audioWinner?.assetId
    || normalizeCutRanges(draft.cuts).length > 0
    || draft.subtitle !== liveSubtitle;
}

/** Sort cut ranges by time and keep only valid, non-overlapping splice bands. */
export function normalizeCutRanges(cuts: readonly TimeRange[], durationMs?: number): TimeRange[] {
  const normalized = cuts.map((cut) => ({ startMs: cut.startMs, endMs: cut.endMs }))
    .sort((left, right) => left.startMs - right.startMs || left.endMs - right.endMs);
  for (const cut of normalized) {
    if (cut.startMs < 0 || cut.endMs < 0) throw new Error("cut range cannot be negative");
    if (cut.endMs <= cut.startMs) throw new Error("cut range cannot be zero-length");
    if (durationMs !== undefined && cut.endMs > durationMs) throw new Error("cut range exceeds duration");
  }
  for (const [index, cut] of normalized.entries()) {
    const previous = normalized[index - 1];
    if (previous && cut.startMs < previous.endMs) throw new Error("cut ranges overlap");
  }
  return normalized;
}

/** Add one accepted cut and retain its selection after chronological sorting. */
export function insertNormalizedCut(
  cuts: readonly TimeRange[],
  candidate: TimeRange,
  durationMs: number,
): { cuts: TimeRange[]; selectedIndex: number } {
  const normalized = normalizeCutRanges([...cuts, candidate], durationMs);
  return {
    cuts: normalized,
    selectedIndex: normalized.findIndex(
      (cut) => cut.startMs === candidate.startMs && cut.endMs === candidate.endMs,
    ),
  };
}

export function buildRecipeDraft(
  line: VoiceLine,
  selectedSource: VoiceSource,
  cuts: readonly TimeRange[],
  subtitle: string | null = null,
): EditRecipeDraft {
  if (line.family === "dialogue_edt") {
    throw new Error("dialogue EDT lines cannot render an audio recipe");
  }
  const normalizedCuts = normalizeCutRanges(cuts);
  return {
    inputAssetId: selectedSource.assetId,
    inputSha256: selectedSource.sha256,
    voiceIndex: line.voiceIndex,
    family: line.family,
    lineId: line.lineId,
    outputExtension: ".ogg",
    operations: normalizedCuts.map((cut) => ({ kind: "cut", startMs: cut.startMs, endMs: cut.endMs })),
    subtitle,
    replacementSource: selectedSource.assetId === line.audioWinner?.assetId
      ? undefined
      : { assetId: selectedSource.assetId, sha256: selectedSource.sha256 },
  };
}
