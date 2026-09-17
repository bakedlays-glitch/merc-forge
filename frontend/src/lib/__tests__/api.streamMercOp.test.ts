/**
 * First frontend tests (the sidecar has ~1000; the frontend had zero).
 * Target: the shared NDJSON stream driver's error contract — the
 * highest-leverage pure-logic surface in lib/api.ts. The Tauri bridge
 * is mocked; `fetch` is stubbed per-test with canned NDJSON bodies.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../tauri", () => ({
  getServerPort: () => Promise.resolve(8000),
  getServerToken: () => Promise.resolve(""),
  onSidecarRestarted: () => () => {},
}));

import {
  ApiError,
  deployVoicePlan,
  formatApiError,
  getVoiceDeploymentHistory,
  preflightVoiceRecipe,
  undoVoiceDeployment,
  updateMercStreaming,
  type SaveProgressEvent,
} from "../api";

function ndjsonResponse(lines: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      for (const l of lines) controller.enqueue(enc.encode(l));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "application/x-ndjson" },
  });
}

describe("streamMercOp (via updateMercStreaming)", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("feeds each line to onProgress and resolves with the done event", async () => {
    vi.mocked(fetch).mockResolvedValue(ndjsonResponse([
      '{"step":"backup","status":"start"}\n',
      '{"step":"backup","status":"done"}\n',
      '{"done":true,"ok":true,"slot":5}\n',
    ]));
    const events: SaveProgressEvent[] = [];
    const fin = await updateMercStreaming(5, {}, undefined, (e) => events.push(e));
    expect(events).toHaveLength(3);
    expect(fin).toEqual({ done: true, ok: true, slot: 5 });
  });

  it("handles a done event split across chunks with no trailing newline", async () => {
    vi.mocked(fetch).mockResolvedValue(ndjsonResponse([
      '{"step":"backup","status":"start"}\n{"done":tr',
      'ue,"ok":true,"slot":9}',
    ]));
    const fin = await updateMercStreaming(9, {}, undefined, () => {});
    expect(fin.slot).toBe(9);
  });

  it("throws a shaped ApiError on {done, ok:false}", async () => {
    vi.mocked(fetch).mockResolvedValue(ndjsonResponse([
      '{"done":true,"ok":false,"error":"SAVE_FAILED_ROLLBACK_FAILED",'
      + '"error_step":"edt","steps_completed":["backup","profiles"]}\n',
    ]));
    const err = await updateMercStreaming(5, {}, undefined, () => {})
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const detail = (err as ApiError).detail as {
      detail: { error: string; error_step: string };
    };
    expect(detail.detail.error).toBe("SAVE_FAILED_ROLLBACK_FAILED");
    expect(detail.detail.error_step).toBe("edt");
  });

  it("surfaces a truncated trailing fragment when the stream dies without done", async () => {
    // Regression: this fragment used to be silently skipped, masking
    // the sidecar's real failure payload.
    vi.mocked(fetch).mockResolvedValue(ndjsonResponse([
      '{"step":"backup","status":"start"}\n',
      '{"done":true,"ok":false,"error":"SAVE_FA',   // truncated mid-write
    ]));
    const err = await updateMercStreaming(5, {}, undefined, () => {})
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const msg = ((err as ApiError).detail as { detail: { message: string } })
      .detail.message;
    expect(msg).toContain("without a 'done' event");
    expect(msg).toContain("SAVE_FA");
  });

  it("throws ApiError(status) on a non-2xx before the stream opens", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(
      JSON.stringify({ error: "AUDIT_FAILED", issues: [] }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    ));
    const err = await updateMercStreaming(5, {}, undefined, () => {})
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(400);
  });
});

describe("formatApiError", () => {
  it("renders something readable for a plain Error", () => {
    expect(formatApiError(new Error("boom"))).toContain("boom");
  });
  it("never returns an empty string for unknown input", () => {
    expect(formatApiError(undefined).length).toBeGreaterThan(0);
    expect(formatApiError({ weird: true }).length).toBeGreaterThan(0);
  });

  it.each([
    ["VOICE_LAB_REQUIRED", "Voice Lab"],
    ["VOICE_SOURCE_CHANGED", "source changed"],
    ["VOICE_TOOLCHAIN_UNAVAILABLE", "toolchain"],
    ["VOICE_RECOVERY_REQUIRED", "recovery"],
    ["VOICE_GAME_RUNNING", "close JA2"],
    ["VOICE_UNDO_CONFLICT", "conflict"],
  ])("formats %s as a friendly Voice Lab error", (code, text) => {
    const error = new ApiError(409, { detail: { error: code } });
    expect(formatApiError(error)).toMatch(new RegExp(text, "i"));
  });
});

describe("Voice Lab deployment API", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preserves the reviewed preflight manifest fields", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({
      plan_id: "plan-1",
      recipe_id: "recipe-1",
      created_utc: "2026-08-15T00:00:00Z",
      evidence_hash: "e".repeat(64),
      affected_profiles: [51, 197],
      targets: [{
        target_id: "speech-111",
        relative_path: "Data-1.13/Speech/197_111.ogg",
        kind: "replace",
        staged_sha256: "o".repeat(64),
        preimage_sha256: "c".repeat(64),
        existed_before: true,
        metadata: { shadowing_variants: [".wav"] },
      }],
      snapshot: {
        backup_pinned: null,
        backup_will_be_pinned: true,
        capabilities: { game_running: false, toolchain_available: true, recovery_required: false },
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    const plan = await preflightVoiceRecipe("recipe-1");

    expect(plan.affectedProfiles).toEqual([51, 197]);
    expect(plan.targetActions[0]).toMatchObject({
      currentSha256: "c".repeat(64),
      outputSha256: "o".repeat(64),
      shadowingVariants: [".wav"],
    });
    expect(plan.backupPinned).toBeNull();
    expect(plan.backupWillBePinned).toBe(true);
    expect(plan.toolchainAvailable).toBe(true);
  });

  it("parses deploy, history, and conflict-aware Undo responses", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({
        deployment_id: "deployment-1", plan_id: "plan-1", recipe_id: "recipe-1",
        backup_id: "backup-1", before_subtitle: null, manifest_path: "opaque-manifest",
        targets: [],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([{
        deployment_id: "deployment-1", recipe_id: "recipe-1", status: "deployed",
        manifest: { backup_id: "backup-1", backup_pinned: true }, targets: [],
      }]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        deployment_id: "deployment-1", status: "UNDO_CONFLICT", message: "target changed",
      }), { status: 200 }));

    expect((await deployVoicePlan("plan-1")).deploymentId).toBe("deployment-1");
    expect((await getVoiceDeploymentHistory())[0]).toMatchObject({ deploymentId: "deployment-1", backupPinned: true });
    expect(await undoVoiceDeployment("deployment-1")).toMatchObject({ status: "UNDO_CONFLICT", message: "target changed" });
  });
});
