import { describe, expect, it } from "vitest";

import {
  buildRecipeDraft,
  describeVoiceLine,
  filterAndSortFindings,
  filterVoiceLines,
  findHighestRiskLine,
  hasVoiceDraftChanges,
  insertNormalizedCut,
  mergeVoiceBankCatalog,
  normalizeCutRanges,
  normalizeVoiceLabSelection,
} from "../voiceLabViewModel";
import type { ImportedVoiceAsset, VoiceFinding, VoiceLine } from "../voiceLabSchema";

function finding(overrides: Partial<VoiceFinding> = {}): VoiceFinding {
  return {
    stableKey: "finding",
    evidenceHash: "evidence",
    code: "MISSING_AUDIO",
    severity: "warning",
    confidence: 0.5,
    state: "needs_review",
    assetIds: [],
    evidence: {},
    suggestedAction: null,
    voiceIndex: null,
    family: null,
    lineId: null,
    evidenceSummary: "Review the captured Voice Lab evidence.",
    ...overrides,
  };
}

describe("filterAndSortFindings", () => {
  it("puts unresolved critical findings before reviewed warnings", () => {
    const result = filterAndSortFindings([
      finding({ stableKey: "b", severity: "warning", state: "intentional" }),
      finding({ stableKey: "a", severity: "critical", state: "needs_review" }),
    ], { states: ["needs_review", "intentional"] });

    expect(result.map((item) => item.stableKey)).toEqual(["a", "b"]);
  });

  it("filters a bank and preserves stable-key ordering for equal risk", () => {
    const result = filterAndSortFindings([
      finding({ stableKey: "z", voiceIndex: 8, confidence: 0.8 }),
      finding({ stableKey: "a", voiceIndex: 8, confidence: 0.8 }),
      finding({ stableKey: "other", voiceIndex: 9, severity: "critical" }),
    ], { bank: 8, states: ["needs_review"] });

    expect(result.map((item) => item.stableKey)).toEqual(["a", "z"]);
  });
});

describe("normalizeVoiceLabSelection", () => {
  it("starts with no merc or line instead of silently choosing the first voice set", () => {
    expect(normalizeVoiceLabSelection(
      { bank: null, line: null },
      [{ voiceIndex: 10, lines: [{ family: "speech", lineId: "001" }] }],
    )).toEqual({ bank: null, line: null });
  });

  it("keeps a valid bank but does not invent an arbitrary line selection", () => {
    expect(normalizeVoiceLabSelection(
      { bank: 11, line: "speech:001" },
      [{ voiceIndex: 10, lines: [{ family: "speech", lineId: "001" }] }],
    )).toEqual({ bank: 10, line: null });
  });
});

describe("mergeVoiceBankCatalog", () => {
  it("shows every merc name immediately and replaces only indexed banks with line data", () => {
    const catalog = [
      { voiceIndex: 15, profiles: [], lines: [] },
      { voiceIndex: 66, profiles: [], lines: [] },
    ];
    const indexed = [{ voiceIndex: 15, profiles: [], lines: [line()] }];

    expect(mergeVoiceBankCatalog(catalog, indexed)).toEqual([
      indexed[0],
      catalog[1],
    ]);
  });
});

describe("line browsing", () => {
  const battle = (): VoiceLine => ({
    ...line(), family: "battle", lineId: "ATTN", triggerByProfileId: {},
  });

  it("searches every line by number, family, trigger name, and meaning", () => {
    const quote = line();
    quote.triggerByProfileId = { 108: { slot: 111, name: "QUOTE_GREETING", meaning: "answers the telephone" } };
    expect(filterVoiceLines([battle(), quote], "telephone").map((item) => item.lineId)).toEqual(["111"]);
    expect(filterVoiceLines([battle(), quote], "attention").map((item) => item.lineId)).toEqual(["ATTN"]);
    expect(filterVoiceLines([battle(), quote], "speech 111").map((item) => item.lineId)).toEqual(["111"]);
  });

  it("uses plain labels while retaining the raw game identifier as detail", () => {
    expect(describeVoiceLine(battle())).toEqual({
      category: "Combat reaction",
      title: "Attention call",
      technicalId: "battle / ATTN",
      explanation: "Played as an attention or alert reaction in combat.",
    });
  });

  it("recognizes named reaction files even when they live in the speech folder", () => {
    const reaction = { ...battle(), family: "speech" as const, lineId: "CURSE" };
    expect(describeVoiceLine(reaction).title).toBe("Frustrated reaction");
    expect(describeVoiceLine(reaction).category).toBe("Voice reaction");
  });

  it("turns engine-oriented trigger notes into plain hiring-call language", () => {
    const hiringLine = line();
    hiringLine.triggerByProfileId = {
      108: {
        slot: 111,
        name: "QUOTE_LENGTH_OF_CONTRACT",
        meaning: "AIM hiring screen: ask player what contract terms they want",
      },
    };
    expect(describeVoiceLine(hiringLine)).toMatchObject({
      title: "Ask what contract terms the player wants",
      explanation: "Used during a hiring call to ask what contract terms the player wants.",
    });
  });
});

describe("risk targeting", () => {
  it("opens the highest-risk unresolved line after analysis", () => {
    const target = findHighestRiskLine([
      finding({ stableKey: "low", voiceIndex: 7, family: "speech", lineId: "2", severity: "low" }),
      finding({ stableKey: "high", voiceIndex: 7, family: "speech", lineId: "1", severity: "high", confidence: .9 }),
      finding({ stableKey: "reviewed", voiceIndex: 7, family: "speech", lineId: "3", severity: "critical", state: "fixed" }),
    ], 7);
    expect(target).toBe("speech:1");
  });
});

function line(): VoiceLine {
  return {
    family: "speech",
    voiceIndex: 108,
    lineId: "111",
    audioWinner: {
      assetId: "live-original",
      family: "speech",
      voiceIndex: 108,
      lineId: "111",
      extension: ".ogg",
      sourceKind: "loose",
      layerRank: 0,
      sizeBytes: 42,
      sha256: "a".repeat(64),
      writable: true,
      winner: true,
    },
    gapWinner: null,
    dialogueEdt: null,
    audioVariants: [],
    gapVariants: [],
    dialogueVariants: [],
    triggerByProfileId: {},
  };
}

function importedAsset(overrides: Partial<ImportedVoiceAsset> = {}): ImportedVoiceAsset {
  return {
    assetId: "import-default",
    installId: "copy",
    extension: ".ogg",
    sourceKind: "import",
    sizeBytes: 84,
    sha256: "b".repeat(64),
    durationMs: 1500,
    ...overrides,
  };
}

describe("normalizeCutRanges", () => {
  it("sorts cuts and rejects overlaps", () => {
    expect(normalizeCutRanges([{ startMs: 900, endMs: 1000 }, { startMs: 100, endMs: 200 }]))
      .toEqual([{ startMs: 100, endMs: 200 }, { startMs: 900, endMs: 1000 }]);
    expect(() => normalizeCutRanges([{ startMs: 100, endMs: 300 }, { startMs: 250, endMs: 400 }]))
      .toThrow("overlap");
  });

  it("rejects negative, empty, and out-of-duration cut ranges", () => {
    expect(() => normalizeCutRanges([{ startMs: -1, endMs: 10 }], 1000)).toThrow("negative");
    expect(() => normalizeCutRanges([{ startMs: 10, endMs: 10 }], 1000)).toThrow("zero-length");
    expect(() => normalizeCutRanges([{ startMs: 10, endMs: 1001 }], 1000)).toThrow("duration");
  });
});

describe("insertNormalizedCut", () => {
  it("selects the just-added cut after normalization reorders it", () => {
    expect(insertNormalizedCut([{ startMs: 900, endMs: 1000 }], { startMs: 100, endMs: 200 }, 1000))
      .toEqual({ cuts: [{ startMs: 100, endMs: 200 }, { startMs: 900, endMs: 1000 }], selectedIndex: 0 });
  });
});

describe("buildRecipeDraft", () => {
  it("uses an explicitly imported source without changing the original", () => {
    const original = line();
    const draft = buildRecipeDraft(original, importedAsset({ assetId: "import-7" }), []);

    expect(draft.inputAssetId).toBe("import-7");
    expect(draft.replacementSource?.assetId).toBe("import-7");
    expect(original.audioWinner?.assetId).toBe("live-original");
  });

  it("keeps an explicit empty subtitle as a clear request", () => {
    expect(buildRecipeDraft(line(), importedAsset(), [], "").subtitle).toBe("");
  });

  it("enables preview only when source, cuts, or subtitle changed", () => {
    expect(hasVoiceDraftChanges(line(), "Stay sharp.", {
      source: line().audioWinner!, cuts: [], subtitle: "Stay sharp.",
    })).toBe(false);
    expect(hasVoiceDraftChanges(line(), "Stay sharp.", {
      source: line().audioWinner!, cuts: [{ startMs: 10, endMs: 20 }], subtitle: "Stay sharp.",
    })).toBe(true);
    expect(hasVoiceDraftChanges(line(), "Stay sharp.", {
      source: importedAsset(), cuts: [], subtitle: "Stay sharp.",
    })).toBe(true);
  });
});
