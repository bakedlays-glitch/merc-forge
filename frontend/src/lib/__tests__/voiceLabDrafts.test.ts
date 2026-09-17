import { describe, expect, it } from "vitest";

import {
  loadVoiceLineDraft,
  removeVoiceLineDraft,
  saveVoiceLineDraft,
  type VoiceLineDraft,
} from "../voiceLabDrafts";

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

const draft: VoiceLineDraft = {
  version: 1,
  installId: "copy",
  voiceIndex: 1,
  family: "speech",
  lineId: "111",
  originalAssetId: "live",
  originalSha256: "a".repeat(64),
  sourceAssetId: "live",
  sourceSha256: "a".repeat(64),
  sourceKind: "loose",
  cuts: [{ startMs: 100, endMs: 200 }],
  subtitle: "New words",
};

describe("Voice Lab drafts", () => {
  it("restores a saved line independently after navigation", () => {
    const storage = new MemoryStorage();
    saveVoiceLineDraft(storage, draft);
    expect(loadVoiceLineDraft(storage, draft, draft.originalSha256)).toEqual(draft);
    expect(loadVoiceLineDraft(storage, { ...draft, lineId: "112" }, draft.originalSha256)).toBeNull();
  });

  it("rejects malformed or stale-source drafts and can reset a line", () => {
    const storage = new MemoryStorage();
    saveVoiceLineDraft(storage, draft);
    expect(loadVoiceLineDraft(storage, draft, "b".repeat(64))).toBeNull();
    storage.setItem("mercforge.voice-lab.draft.v1/copy/1/speech/112", "not json");
    expect(loadVoiceLineDraft(storage, { ...draft, lineId: "112" }, draft.originalSha256)).toBeNull();
    removeVoiceLineDraft(storage, draft);
    expect(loadVoiceLineDraft(storage, draft, draft.originalSha256)).toBeNull();
  });
});
