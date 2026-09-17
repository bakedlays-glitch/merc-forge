/**
 * Voice Lab's public wire boundary. The sidecar deliberately removes local
 * asset locators before responses reach this module; keep that constraint in
 * these types so UI code cannot accidentally surface a filesystem path.
 */

export type VoiceFindingSeverity = "critical" | "high" | "warning" | "medium" | "low" | "info";
export type VoiceFindingState =
  | "needs_review"
  | "accepted"
  | "fixed"
  | "intentional"
  | "false_positive"
  | "deferred";
export type VoiceLineFamily = "speech" | "battle" | "dialogue_edt";
/** Assets also include generated lip-sync timing companions. */
export type VoiceAssetFamily = VoiceLineFamily | "gap";

export interface VoiceLabStatus {
  installId: string;
  scanWorkers: number;
  transcriptionWorkers: number;
  authoringWorkspaceConfigured: boolean;
  ffmpegConfigured: boolean;
  transcriberConfigured: boolean;
  hasSnapshot: boolean;
  deployment: VoiceDeploymentCapabilities;
}

/** Unknown status is deliberately distinct from a safe/available result. */
export interface VoiceDeploymentCapabilities {
  gameRunning: boolean | null;
  toolchainAvailable: boolean | null;
  recoveryRequired: boolean | null;
}

export interface VoiceLabJob {
  jobId: string;
  kind: string;
  status: "queued" | "running" | "cancelling" | "cancelled" | "complete" | "failed";
  completed: number;
  total: number;
  lastMessage: string;
  cancelRequested: boolean;
  previewAssetId?: string;
}

export interface VoiceProfile {
  profileId: number;
  profileType: number;
  name: string;
  nickname: string;
  faceIndex: number | null;
  voiceIndex: number;
}

export interface VoiceAsset {
  assetId: string;
  family: VoiceAssetFamily;
  voiceIndex: number;
  lineId: string;
  extension: string;
  sourceKind: "loose" | "slf";
  layerRank: number;
  sizeBytes: number;
  sha256: string;
  writable: boolean;
  winner: boolean;
}

/** A durable upload. Deliberately has no filesystem locator. */
export interface ImportedVoiceAsset {
  assetId: string;
  installId: string;
  extension: string;
  sourceKind: "import";
  sizeBytes: number;
  sha256: string;
  durationMs: number;
}

export type VoiceSource = VoiceAsset | ImportedVoiceAsset;

export interface TriggerMeaning {
  slot: number;
  name: string;
  meaning: string;
}

export interface TimeRange {
  startMs: number;
  endMs: number;
}

export interface VoiceWaveform {
  assetId: string;
  durationMs: number;
  peaks: number[];
}
export interface VoiceLineDetail { subtitle: string | null; transcription: { text: string; confidence: number; modelId: string; modelVersion: string } | null; }

/** A deploy target intentionally carries only an install-relative path. */
export interface VoiceDeployTargetAction {
  targetId: string;
  relativePath: string;
  kind: "replace" | "create" | "remove_shadowing_variant";
  currentSha256: string | null;
  outputSha256: string | null;
  existedBefore: boolean;
  shadowingVariants: string[];
}

export interface VoiceDeployPlan {
  planId: string;
  recipeId: string;
  createdUtc: string;
  evidenceHash: string;
  affectedProfiles: number[];
  targetActions: VoiceDeployTargetAction[];
  /** Every Voice Lab deployment snapshot is retained for conflict-aware Undo. */
  backupPinned: boolean | null;
  backupWillBePinned: boolean;
  gameRunning: boolean | null;
  toolchainAvailable: boolean | null;
  recoveryRequired: boolean | null;
}

export interface VoiceDeployment {
  deploymentId: string;
  recipeId: string;
  status: string;
  backupPinned: boolean;
  targetActions: VoiceDeployTargetAction[];
}

export interface VoiceUndoResult {
  deploymentId: string;
  status: "undone" | "UNDO_CONFLICT";
  message: string | null;
}

export interface EditRecipeDraft {
  inputAssetId: string;
  inputSha256: string;
  voiceIndex: number;
  family: "speech" | "battle";
  lineId: string;
  outputExtension: ".ogg";
  operations: Array<{ kind: "cut"; startMs: number; endMs: number }>;
  subtitle: string | null;
  replacementSource?: { assetId: string; sha256: string };
}

export interface VoiceLine {
  family: VoiceLineFamily;
  voiceIndex: number;
  lineId: string;
  audioWinner: VoiceAsset | null;
  gapWinner: VoiceAsset | null;
  dialogueEdt: VoiceAsset | null;
  audioVariants: VoiceAsset[];
  gapVariants: VoiceAsset[];
  dialogueVariants: VoiceAsset[];
  triggerByProfileId: Record<number, TriggerMeaning>;
}

export interface VoiceBank {
  voiceIndex: number;
  profiles: VoiceProfile[];
  lines: VoiceLine[];
}

export interface VoiceFinding {
  stableKey: string;
  evidenceHash: string;
  code: string;
  severity: VoiceFindingSeverity;
  confidence: number;
  state: VoiceFindingState;
  assetIds: string[];
  evidence: Record<string, unknown>;
  suggestedAction: string | null;
  voiceIndex: number | null;
  family: VoiceLineFamily | null;
  lineId: string | null;
  evidenceSummary: string;
}

type JsonRecord = Record<string, unknown>;

function record(value: unknown, label: string): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Voice Lab returned an invalid ${label}.`);
  }
  return value as JsonRecord;
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string") throw new Error(`Voice Lab returned an invalid ${label}.`);
  return value;
}

function number(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Voice Lab returned an invalid ${label}.`);
  }
  return value;
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw new Error(`Voice Lab returned an invalid ${label}.`);
  return value;
}

function nullableBoolean(value: unknown, label: string): boolean | null {
  if (value === undefined || value === null) return null;
  return boolean(value, label);
}

function optionalString(value: unknown, label: string): string | null {
  return value === null ? null : string(value, label);
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`Voice Lab returned an invalid ${label}.`);
  return value;
}

function lineFamily(value: unknown): VoiceLineFamily {
  const parsed = string(value, "line family");
  if (parsed === "speech" || parsed === "battle" || parsed === "dialogue_edt") return parsed;
  throw new Error("Voice Lab returned an invalid line family.");
}

function assetFamily(value: unknown): VoiceAssetFamily {
  const parsed = string(value, "asset family");
  if (parsed === "gap") return parsed;
  try {
    return lineFamily(parsed);
  } catch {
    throw new Error("Voice Lab returned an invalid asset family.");
  }
}

function severity(value: unknown): VoiceFindingSeverity {
  const parsed = string(value, "finding severity");
  if (["critical", "high", "warning", "medium", "low", "info"].includes(parsed)) {
    return parsed as VoiceFindingSeverity;
  }
  throw new Error("Voice Lab returned an invalid finding severity.");
}

function findingState(value: unknown): VoiceFindingState {
  const parsed = string(value, "finding state");
  if (["needs_review", "accepted", "fixed", "intentional", "false_positive", "deferred"].includes(parsed)) {
    return parsed as VoiceFindingState;
  }
  throw new Error("Voice Lab returned an invalid finding state.");
}

function sourceKind(value: unknown): VoiceAsset["sourceKind"] {
  const parsed = string(value, "asset source kind");
  if (parsed === "loose" || parsed === "slf") return parsed;
  throw new Error("Voice Lab returned an invalid asset source kind.");
}

function parseAsset(value: unknown): VoiceAsset {
  const wire = record(value, "asset");
  return {
    assetId: string(wire.asset_id, "asset id"),
    family: assetFamily(wire.family),
    voiceIndex: number(wire.voice_index, "asset voice index"),
    lineId: string(wire.line_id, "asset line id"),
    extension: string(wire.extension, "asset extension"),
    sourceKind: sourceKind(wire.source_kind),
    layerRank: number(wire.layer_rank, "asset layer rank"),
    sizeBytes: number(wire.size_bytes, "asset size"),
    sha256: string(wire.sha256, "asset hash"),
    writable: boolean(wire.writable, "asset writable flag"),
    winner: boolean(wire.winner, "asset winner flag"),
  };
}

function parseImportedAsset(value: unknown): ImportedVoiceAsset {
  const wire = record(value, "imported asset");
  if (wire.source_kind !== "import") throw new Error("Voice Lab returned an invalid imported source.");
  return {
    assetId: string(wire.asset_id, "imported asset id"),
    installId: string(wire.install_id, "imported asset install id"),
    extension: string(wire.extension, "imported asset extension"),
    sourceKind: "import",
    sizeBytes: number(wire.size_bytes, "imported asset size"),
    sha256: string(wire.sha256, "imported asset hash"),
    durationMs: number(wire.duration_ms, "imported asset duration"),
  };
}

function parseTriggerMeaning(value: unknown): TriggerMeaning {
  const wire = record(value, "trigger meaning");
  return {
    slot: number(wire.slot, "trigger slot"),
    name: string(wire.name, "trigger name"),
    meaning: string(wire.meaning, "trigger meaning"),
  };
}

function parseTriggerMap(value: unknown): Record<number, TriggerMeaning> {
  const wire = record(value, "trigger map");
  return Object.fromEntries(Object.entries(wire).map(([profileId, meaning]) => {
    const parsedProfileId = Number(profileId);
    if (!Number.isInteger(parsedProfileId)) throw new Error("Voice Lab returned an invalid trigger profile id.");
    return [parsedProfileId, parseTriggerMeaning(meaning)];
  }));
}

function parseProfile(value: unknown): VoiceProfile {
  const wire = record(value, "profile");
  const faceIndex = wire.face_index;
  if (faceIndex !== null && (typeof faceIndex !== "number" || !Number.isFinite(faceIndex))) {
    throw new Error("Voice Lab returned an invalid profile face index.");
  }
  return {
    profileId: number(wire.profile_id, "profile id"),
    profileType: number(wire.profile_type, "profile type"),
    name: string(wire.name, "profile name"),
    nickname: string(wire.nickname, "profile nickname"),
    faceIndex,
    voiceIndex: number(wire.voice_index, "profile voice index"),
  };
}

function parseLine(value: unknown): VoiceLine {
  const wire = record(value, "line");
  return {
    family: lineFamily(wire.family),
    voiceIndex: number(wire.voice_index, "line voice index"),
    lineId: string(wire.line_id, "line id"),
    audioWinner: wire.audio_winner === null ? null : parseAsset(wire.audio_winner),
    gapWinner: wire.gap_winner === null ? null : parseAsset(wire.gap_winner),
    dialogueEdt: wire.dialogue_edt === null ? null : parseAsset(wire.dialogue_edt),
    audioVariants: array(wire.audio_variants, "audio variants").map(parseAsset),
    gapVariants: array(wire.gap_variants, "gap variants").map(parseAsset),
    dialogueVariants: array(wire.dialogue_variants, "dialogue variants").map(parseAsset),
    triggerByProfileId: parseTriggerMap(wire.trigger_by_profile_id),
  };
}

function extractTarget(evidence: JsonRecord): Pick<VoiceFinding, "voiceIndex" | "family" | "lineId"> {
  const candidates = [evidence, recordOrNull(evidence.line), recordOrNull(evidence.bank), firstRecord(evidence.audit_lines)];
  for (const candidate of candidates) {
    if (!candidate) continue;
    const voiceIndex = typeof candidate.voice_index === "number" ? candidate.voice_index : null;
    const candidateFamily = candidate.family;
    const candidateLineId = candidate.line_id;
    if (voiceIndex === null && candidateLineId === undefined) continue;
    return {
      voiceIndex,
      family: candidateFamily === "speech" || candidateFamily === "battle" || candidateFamily === "dialogue_edt"
        ? candidateFamily
        : null,
      lineId: typeof candidateLineId === "string" ? candidateLineId : null,
    };
  }
  return { voiceIndex: null, family: null, lineId: null };
}

function recordOrNull(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : null;
}

function firstRecord(value: unknown): JsonRecord | null {
  return Array.isArray(value) ? recordOrNull(value[0]) : null;
}

export function parseVoiceLabStatus(value: unknown): VoiceLabStatus {
  const wire = record(value, "status");
  const deployment = wire.deployment === undefined ? {} : record(wire.deployment, "deployment status");
  return {
    installId: string(wire.install_id, "install id"),
    scanWorkers: number(wire.scan_workers, "scan worker count"),
    transcriptionWorkers: number(wire.transcription_workers, "transcription worker count"),
    authoringWorkspaceConfigured: boolean(wire.authoring_workspace_configured, "authoring workspace state"),
    ffmpegConfigured: boolean(wire.ffmpeg_configured, "FFmpeg state"),
    transcriberConfigured: boolean(wire.transcriber_configured, "transcriber state"),
    hasSnapshot: boolean(wire.has_snapshot, "inventory state"),
    deployment: {
      gameRunning: nullableBoolean(deployment.game_running, "game running state"),
      toolchainAvailable: nullableBoolean(deployment.toolchain_available, "toolchain state"),
      recoveryRequired: nullableBoolean(deployment.recovery_required, "recovery state"),
    },
  };
}

export function parseVoiceLabJob(value: unknown): VoiceLabJob {
  const wire = record(value, "job");
  const status = string(wire.status, "job status");
  if (!["queued", "running", "cancelling", "cancelled", "complete", "failed"].includes(status)) {
    throw new Error("Voice Lab returned an invalid job status.");
  }
  return {
    jobId: string(wire.job_id, "job id"),
    kind: string(wire.kind, "job kind"),
    status: status as VoiceLabJob["status"],
    completed: number(wire.completed, "job completion count"),
    total: number(wire.total, "job total"),
    lastMessage: string(wire.last_message, "job message"),
    cancelRequested: boolean(wire.cancel_requested, "job cancellation state"),
    ...(wire.preview_asset_id === undefined ? {} : { previewAssetId: string(wire.preview_asset_id, "preview asset id") }),
  };
}

export function parseVoiceBanks(value: unknown): VoiceBank[] {
  return array(value, "bank list").map((item) => {
    const wire = record(item, "bank");
    return {
      voiceIndex: number(wire.voice_index, "bank voice index"),
      profiles: array(wire.profiles, "bank profiles").map(parseProfile),
      lines: array(wire.lines, "bank lines").map(parseLine),
    };
  });
}

export function parseVoiceFindings(value: unknown): VoiceFinding[] {
  return array(value, "finding list").map((item) => {
    const wire = record(item, "finding");
    const evidence = record(wire.evidence, "finding evidence");
    const target = extractTarget(evidence);
    const plainLanguage = typeof evidence.plain_language === "string" ? evidence.plain_language : null;
    return {
      stableKey: string(wire.stable_key, "finding key"),
      evidenceHash: string(wire.evidence_hash, "finding evidence hash"),
      code: string(wire.code, "finding code"),
      severity: severity(wire.severity),
      confidence: number(wire.confidence, "finding confidence"),
      state: findingState(wire.state),
      assetIds: array(wire.asset_ids, "finding asset ids").map((assetId) => string(assetId, "finding asset id")),
      evidence,
      suggestedAction: optionalString(wire.suggested_action, "suggested action"),
      ...target,
      evidenceSummary: plainLanguage ?? "Review the captured Voice Lab evidence.",
    };
  });
}

export function parseVoiceWaveform(value: unknown): VoiceWaveform {
  const wire = record(value, "waveform");
  return {
    assetId: string(wire.asset_id, "waveform asset id"),
    durationMs: number(wire.duration_ms, "waveform duration"),
    peaks: array(wire.peaks, "waveform peaks").map((peak) => number(peak, "waveform peak")),
  };
}

export function parseImportedVoiceAsset(value: unknown): ImportedVoiceAsset {
  return parseImportedAsset(value);
}
export function parseVoiceLineDetail(value: unknown): VoiceLineDetail {
  const wire = record(value, "line detail"); const transcript = wire.transcription;
  return { subtitle: optionalString(wire.subtitle, "subtitle"), transcription: transcript === null ? null : { text: string(record(transcript, "transcription").text, "transcript text"), confidence: number(record(transcript, "transcription").confidence, "transcript confidence"), modelId: string(record(transcript, "transcription").model_id, "transcript model"), modelVersion: string(record(transcript, "transcription").model_version, "transcript model version") } };
}

function parseDeployTargetAction(value: unknown): VoiceDeployTargetAction {
  const wire = record(value, "deployment target");
  const kind = string(wire.kind, "deployment target kind");
  if (kind !== "replace" && kind !== "create" && kind !== "remove_shadowing_variant") {
    throw new Error("Voice Lab returned an invalid deployment target kind.");
  }
  const metadata = wire.metadata === undefined ? {} : record(wire.metadata, "deployment target metadata");
  const variants = metadata.shadowing_variants;
  return {
    targetId: string(wire.target_id, "deployment target id"),
    relativePath: string(wire.relative_path, "deployment target path"),
    kind,
    currentSha256: wire.preimage_sha256 === null || wire.preimage_sha256 === undefined
      ? null : string(wire.preimage_sha256, "deployment target current hash"),
    outputSha256: wire.staged_sha256 === null || wire.staged_sha256 === undefined
      ? null : string(wire.staged_sha256, "deployment target output hash"),
    existedBefore: boolean(wire.existed_before, "deployment target existence"),
    shadowingVariants: variants === undefined ? [] : array(variants, "shadowing variants")
      .map((variant) => string(variant, "shadowing variant")),
  };
}

export function parseVoiceDeployPlan(value: unknown): VoiceDeployPlan {
  const wire = record(value, "deployment preflight");
  const snapshot = wire.snapshot === undefined ? {} : record(wire.snapshot, "deployment snapshot");
  const capabilities = snapshot.capabilities === undefined ? {} : record(snapshot.capabilities, "deployment capabilities");
  return {
    planId: string(wire.plan_id, "deployment plan id"),
    recipeId: string(wire.recipe_id, "deployment recipe id"),
    createdUtc: string(wire.created_utc, "deployment creation time"),
    evidenceHash: string(wire.evidence_hash, "deployment evidence hash"),
    affectedProfiles: array(wire.affected_profiles, "affected profiles")
      .map((profile) => number(profile, "affected profile")),
    targetActions: array(wire.targets, "deployment targets").map(parseDeployTargetAction),
    backupPinned: nullableBoolean(snapshot.backup_pinned, "deployment backup state"),
    backupWillBePinned: snapshot.backup_will_be_pinned === undefined
      ? false : boolean(snapshot.backup_will_be_pinned, "future deployment backup state"),
    gameRunning: nullableBoolean(capabilities.game_running, "game running state"),
    toolchainAvailable: nullableBoolean(capabilities.toolchain_available, "toolchain state"),
    recoveryRequired: nullableBoolean(capabilities.recovery_required, "recovery state"),
  };
}

export function parseVoiceDeployment(value: unknown): VoiceDeployment {
  const wire = record(value, "deployment history item");
  const manifest = wire.manifest === undefined ? {} : record(wire.manifest, "deployment manifest");
  return {
    deploymentId: string(wire.deployment_id, "deployment id"),
    recipeId: string(wire.recipe_id, "deployment recipe id"),
    status: string(wire.status, "deployment status"),
    backupPinned: nullableBoolean(manifest.backup_pinned, "deployment backup state") === true,
    targetActions: array(wire.targets, "deployment targets").map(parseDeployTargetAction),
  };
}

export function parseVoiceDeployments(value: unknown): VoiceDeployment[] {
  return array(value, "deployment history").map(parseVoiceDeployment);
}

export function parseVoiceUndoResult(value: unknown): VoiceUndoResult {
  const wire = record(value, "Undo result");
  const status = string(wire.status, "Undo status");
  if (status !== "undone" && status !== "UNDO_CONFLICT") {
    throw new Error("Voice Lab returned an invalid Undo status.");
  }
  return {
    deploymentId: string(wire.deployment_id, "Undo deployment id"),
    status,
    message: wire.message === undefined ? null : optionalString(wire.message, "Undo message"),
  };
}
