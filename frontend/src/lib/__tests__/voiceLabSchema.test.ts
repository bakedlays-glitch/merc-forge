import { describe, expect, it } from "vitest";

import { parseVoiceBanks, parseVoiceLineDetail } from "../voiceLabSchema";

function bankWithGapWinner() {
  return {
    voice_index: 108,
    profiles: [{
      profile_id: 108,
      profile_type: 0,
      name: "Sulik",
      nickname: "Tribal",
      face_index: 108,
      voice_index: 108,
    }],
    lines: [{
      family: "speech",
      voice_index: 108,
      line_id: "001",
      audio_winner: null,
      gap_winner: {
        asset_id: "gap-108-001",
        family: "gap",
        voice_index: 108,
        line_id: "001",
        extension: ".gap",
        source_kind: "loose",
        layer_rank: 0,
        size_bytes: 64,
        sha256: "a".repeat(64),
        writable: true,
        winner: true,
      },
      dialogue_edt: null,
      audio_variants: [],
      gap_variants: [],
      dialogue_variants: [],
      trigger_by_profile_id: {},
    }],
  };
}

describe("parseVoiceBanks", () => {
  it("normalizes a Task 8 bank with a GAP winner", () => {
    const [bank] = parseVoiceBanks([bankWithGapWinner()]);

    expect(bank?.lines[0]?.gapWinner).toMatchObject({
      assetId: "gap-108-001",
      family: "gap",
    });
  });

  it("rejects an unknown asset family", () => {
    const wire = bankWithGapWinner();
    const gapWinner = wire.lines[0]?.gap_winner;
    if (gapWinner) gapWinner.family = "filesystem_path";

    expect(() => parseVoiceBanks([wire])).toThrow("invalid asset family");
  });
});

describe("parseVoiceLineDetail", () => {
  it("preserves a decoded empty subtitle separately from no subtitle change", () => {
    const detail = parseVoiceLineDetail({
      subtitle: "",
      transcription: { text: "Stay sharp.", confidence: .92, model_id: "tiny", model_version: "1" },
    });

    expect(detail.subtitle).toBe("");
    expect(detail.transcription).toMatchObject({ text: "Stay sharp.", confidence: .92, modelId: "tiny" });
    expect(parseVoiceLineDetail({ subtitle: null, transcription: null }).subtitle).toBeNull();
  });
});
