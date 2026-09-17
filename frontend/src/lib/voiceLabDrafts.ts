import type { TimeRange, VoiceLineFamily } from "./voiceLabSchema";

export interface VoiceLineDraftIdentity {
  installId: string;
  voiceIndex: number;
  family: VoiceLineFamily;
  lineId: string;
}

export interface VoiceLineDraft extends VoiceLineDraftIdentity {
  version: 1;
  originalAssetId: string;
  originalSha256: string;
  sourceAssetId: string;
  sourceSha256: string;
  sourceKind: "loose" | "slf" | "import";
  cuts: TimeRange[];
  subtitle: string | null;
}

function draftKey(identity: VoiceLineDraftIdentity): string {
  return [
    "mercforge.voice-lab.draft.v1",
    encodeURIComponent(identity.installId),
    identity.voiceIndex,
    encodeURIComponent(identity.family),
    encodeURIComponent(identity.lineId),
  ].join("/");
}

function isDraft(value: unknown): value is VoiceLineDraft {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const draft = value as Partial<VoiceLineDraft>;
  return draft.version === 1
    && typeof draft.installId === "string"
    && Number.isInteger(draft.voiceIndex)
    && (draft.family === "speech" || draft.family === "battle" || draft.family === "dialogue_edt")
    && typeof draft.lineId === "string"
    && typeof draft.originalAssetId === "string"
    && typeof draft.originalSha256 === "string"
    && typeof draft.sourceAssetId === "string"
    && typeof draft.sourceSha256 === "string"
    && (draft.sourceKind === "loose" || draft.sourceKind === "slf" || draft.sourceKind === "import")
    && Array.isArray(draft.cuts)
    && draft.cuts.every((cut) => Number.isFinite(cut.startMs) && Number.isFinite(cut.endMs))
    && (draft.subtitle === null || typeof draft.subtitle === "string");
}

export function loadVoiceLineDraft(
  storage: Storage,
  identity: VoiceLineDraftIdentity,
  currentOriginalSha256: string,
): VoiceLineDraft | null {
  const raw = storage.getItem(draftKey(identity));
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isDraft(parsed) || parsed.originalSha256 !== currentOriginalSha256) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveVoiceLineDraft(storage: Storage, draft: VoiceLineDraft): void {
  storage.setItem(draftKey(draft), JSON.stringify(draft));
}

export function removeVoiceLineDraft(storage: Storage, identity: VoiceLineDraftIdentity): void {
  storage.removeItem(draftKey(identity));
}
