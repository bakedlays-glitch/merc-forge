import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  autoPositionFaceGear,
  getApiBaseUrl,
  getFaceGearCapacity,
  injectFaceGearOverlay,
  nudgeFaceGearOffset,
  previewFaceGearOverlay,
  setFaceGearOffset,
} from "../lib/api";
import { getServerToken } from "../lib/tauri";

interface Props {
  /** Slot number — used to fetch the merc's SmallFace so each gear row can
   *  composite its overlay over the real portrait, like the engine does. */
  slot: number;
  faceIndex: number;
  /** Merc's eye coordinates from MercProfiles.xml — drives the auto-position
   *  delta. The wizard reads source-merc eye coords from the install on the
   *  server side; only the target's coords need to come in here. */
  eyeX: number;
  eyeY: number;
}

/**
 * Per-merc FaceGear overlay authoring.
 *
 * For each non-IMP FaceGear STI in the install, shows the merc's current
 * frame[face_index] and lets the user upload a new image (48×43 or larger;
 * larger is center-cropped + scaled down to 48×43) to replace it.
 * Writes mirror to the matching `_IMP.sti` partner so IMP-Type mercs render
 * the same hat. Each write is backed up first.
 *
 * Colors quantize against the STI's existing palette — overlays with
 * radically new color universes look "close" to ideal, not exact. FaceGear
 * art is typically simple enough that this is fine.
 */
export default function FaceGearOverlayAuthor({ slot, faceIndex, eyeX, eyeY }: Props) {
  const capacity = useQuery({
    queryKey: ["facegear-capacity"],
    queryFn: () => getFaceGearCapacity(),
    staleTime: 5 * 60 * 1000,
  });

  // The merc's SmallFace, fetched ONCE as a blob and shared by every gear
  // row's in-game composite preview. Fetched (not <img src>) so the auth
  // token rides a header and a 204 "no portrait" resolves to null instead
  // of a broken image — rows fall back to the bare gear frame.
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    Promise.all([getApiBaseUrl(), getServerToken()])
      .then(([base, token]) =>
        fetch(`${base}/merc/${slot}/portrait?size=smallface&v=${Date.now()}`, {
          headers: token ? { "X-MercWizard-Token": token } : {},
        }),
      )
      .then(async (res) => {
        if (cancelled || res.status !== 200) return;
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPortraitUrl(objectUrl);
      })
      .catch(() => {
        // No portrait / no sidecar — rows show the bare gear frame.
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [slot]);

  const items = useMemo(() => {
    if (!capacity.data) return [];
    // A gear name can exist in several data layers (e.g. Data/ AND
    // Data-UB/ both ship Face_GasMask.sti). Every sidecar endpoint
    // resolves `sti_name` to the FIRST enumerated match, so duplicate
    // rows would preview and write the exact same file — keep only the
    // first occurrence and note how many shadowed copies exist.
    const seen = new Map<string, { item: (typeof capacity.data.items)[number]; shadowed: number }>();
    for (const i of capacity.data.items) {
      if (i.is_imp_variant) continue;
      const key = i.name.toLowerCase();
      const entry = seen.get(key);
      if (entry) entry.shadowed += 1;
      else seen.set(key, { item: i, shadowed: 0 });
    }
    return [...seen.values()].sort((a, b) => a.item.name.localeCompare(b.item.name));
  }, [capacity.data]);

  if (capacity.isLoading) {
    return <p className="text-xs text-wasteland-500 mt-4">Loading FaceGear items…</p>;
  }
  if (capacity.isError) {
    return <p className="text-xs text-rust-400 mt-4">Couldn't load FaceGear list.</p>;
  }
  if (items.length === 0) {
    return (
      <p className="text-xs text-wasteland-500 mt-4">
        No FaceGear STIs in this install — no overlays to author.
      </p>
    );
  }

  return (
    <div className="mt-4 space-y-3">
      <h3 className="text-xs font-semibold uppercase text-wasteland-400">
        Custom overlays for this merc (face index {faceIndex})
      </h3>
      <p className="text-xs text-wasteland-500">
        Two workflows per item: <span className="text-wasteland-300">Auto</span> copies a stock frame
        from the install and positions it relative to your merc's eye coords ({eyeX}, {eyeY}) — zero
        art needed, you can fine-tune position afterward with the X/Y inputs or the ←↑↓→ arrows.{" "}
        <span className="text-wasteland-300">Upload</span> takes your own image (48×43 or larger;
        bigger images are cropped and scaled down to fit) and recolors it to match this gear's
        palette, so colors may shift slightly. Both mirror to{" "}
        <code className="font-mono">_IMP.sti</code> and back up first. Reversible from the Backups page.
      </p>
      <div className="space-y-2">
        {items.map(({ item, shadowed }) => (
          <OverlayItemRow
            key={item.relative_path}
            stiName={item.name}
            relativePath={item.relative_path}
            shadowedCopies={shadowed}
            faceIndex={faceIndex}
            eyeX={eyeX}
            eyeY={eyeY}
            currentFrameCount={item.frame_count}
            portraitUrl={portraitUrl}
          />
        ))}
      </div>
    </div>
  );
}

function OverlayItemRow({
  stiName,
  relativePath,
  shadowedCopies,
  faceIndex,
  eyeX,
  eyeY,
  currentFrameCount,
  portraitUrl,
}: {
  stiName: string;
  relativePath: string;
  shadowedCopies: number;
  faceIndex: number;
  eyeX: number;
  eyeY: number;
  currentFrameCount: number;
  portraitUrl: string | null;
}) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const current = useQuery({
    queryKey: ["facegear-overlay", relativePath, faceIndex],
    queryFn: () => previewFaceGearOverlay(stiName, faceIndex, undefined, relativePath),
    // Refetch when the frame is overwritten
    staleTime: 30 * 1000,
  });

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("Pick a PNG first");
      return injectFaceGearOverlay(stiName, faceIndex, file, undefined, true, relativePath);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["facegear-capacity"] });
      qc.invalidateQueries({ queryKey: ["facegear-overlay", relativePath, faceIndex] });
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
    },
  });

  const autoPos = useMutation({
    mutationFn: () => autoPositionFaceGear(stiName, faceIndex, eyeX, eyeY, { relative_path: relativePath }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["facegear-capacity"] });
      qc.invalidateQueries({ queryKey: ["facegear-overlay", relativePath, faceIndex] });
    },
  });

  // Track the most recently-applied offset so the nudge buttons can
  // show the live total instead of just the delta from auto-position.
  // Initial value comes from autoPos.data when present; each successful
  // nudge updates it to nudge.data.nudged[0].new_offset_xy.
  const nudge = useMutation({
    mutationFn: ({ dx, dy }: { dx: number; dy: number }) =>
      nudgeFaceGearOffset(stiName, faceIndex, dx, dy, undefined, true, relativePath),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["facegear-capacity"] });
      qc.invalidateQueries({ queryKey: ["facegear-overlay", relativePath, faceIndex] });
    },
  });

  // Direct-coord-edit mutation. The X/Y inputs below the row call this
  // with absolute values; the ←↑↓→ arrows still go through `nudge`. Both
  // routes write through the same set_overlay_offset / nudge_overlay_offset
  // primitives in the sidecar, so the on-disk encoding is identical.
  const setOffset = useMutation({
    mutationFn: ({ x, y }: { x: number; y: number }) =>
      setFaceGearOffset(stiName, faceIndex, x, y, undefined, true, relativePath),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["facegear-capacity"] });
      qc.invalidateQueries({ queryKey: ["facegear-overlay", relativePath, faceIndex] });
    },
  });

  // What offset is "live" right now? Prefer the most recent session
  // mutation (setOffset > nudge > auto-position), then fall back to the
  // frame's baked-in offset from disk (read via the preview query). The
  // disk fallback is what makes the offset widget visible for frames
  // authored in a prior session — without it, the widget was hidden
  // until the user clicked Auto / nudge / setOffset first.
  const liveOffset = setOffset.data?.written[0]?.new_offset_xy
    ?? nudge.data?.nudged[0]?.new_offset_xy
    ?? autoPos.data?.written[0]?.applied_offset_xy
    ?? current.data?.offset_xy
    ?? null;

  const inRange = faceIndex < currentFrameCount;
  const currentDataUrl = current.data?.png_b64
    ? `data:image/png;base64,${current.data.png_b64}`
    : null;

  return (
    <div className="rounded border border-wasteland-700 bg-wasteland-800/30 px-3 py-2 flex items-center gap-3 flex-wrap">
      <div className="flex-shrink-0">
        {currentDataUrl && portraitUrl ? (
          /* In-game composite: the gear frame pasted over the merc's
             SmallFace at its stored (sOffsetX, sOffsetY) — the same
             recipe the engine uses when rendering equipped FaceGear.
             Shown at 2× so eye alignment is readable; re-renders live
             as Auto / nudge / X-Y edits change the offset. */
          <div
            className="relative overflow-hidden rounded border border-wasteland-700 bg-wasteland-900"
            style={{ width: 96, height: 86 }}
            title="In-game preview: gear over this merc's SmallFace at its stored offset"
          >
            <div
              className="relative"
              style={{ width: 48, height: 43, transform: "scale(2)", transformOrigin: "top left" }}
            >
              <img
                src={portraitUrl}
                alt="merc SmallFace"
                width={48}
                height={43}
                className="absolute left-0 top-0"
                style={{ imageRendering: "pixelated" }}
              />
              <img
                src={currentDataUrl}
                alt={`current ${stiName} frame ${faceIndex}`}
                className="absolute"
                style={{
                  left: liveOffset?.[0] ?? 0,
                  top: liveOffset?.[1] ?? 0,
                  imageRendering: "pixelated",
                }}
              />
            </div>
          </div>
        ) : currentDataUrl ? (
          <img
            src={currentDataUrl}
            alt={`current ${stiName} frame ${faceIndex}`}
            className="w-12 h-11 object-contain rounded border border-wasteland-700 bg-wasteland-900"
            style={{ imageRendering: "pixelated" }}
          />
        ) : (
          <div className="w-12 h-11 rounded border border-dashed border-wasteland-700 bg-wasteland-900 text-[10px] text-wasteland-600 flex items-center justify-center text-center">
            {inRange ? "blank" : "out of range"}
          </div>
        )}
      </div>
      <div className="flex-1 min-w-[10rem]">
        <div className="font-mono text-xs text-wasteland-200 truncate">{stiName}</div>
        <div className="text-[10px] text-wasteland-500">
          {currentFrameCount} frames · slot {faceIndex} {inRange ? "in range" : "OUT OF RANGE (will extend)"}
        </div>
        <div className="text-[10px] text-wasteland-600 truncate" title={relativePath}>
          {relativePath.replace(/\\/g, "/").split("/").slice(0, -1).join("/")}
          {shadowedCopies > 0 && (
            <span
              className="text-amber-500/80"
              title="Same filename exists in other data layers — edits here target this copy (the one every write resolves to); the shadowed copies are untouched."
            >
              {" "}· +{shadowedCopies} shadowed cop{shadowedCopies === 1 ? "y" : "ies"}
            </span>
          )}
        </div>
      </div>
      <div className="flex-shrink-0 flex items-center gap-2">
        {previewUrl && (
          <img
            src={previewUrl}
            alt="preview"
            className="w-12 h-11 object-contain rounded border border-rust-500/60 bg-wasteland-900"
            style={{ imageRendering: "pixelated" }}
          />
        )}
        <input
          ref={inputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/bmp"
          className="hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button
          type="button"
          className="text-xs px-2 py-1 rounded border border-blue-500/70 text-blue-200 hover:bg-blue-500/10 disabled:opacity-50"
          disabled={autoPos.isPending}
          onClick={() => autoPos.mutate()}
          title="Copy the first non-empty frame from this install and shift its offset by your merc's eye-coord delta. Fine-tune position with the X/Y inputs or ←↑↓→ arrows below after running."
        >
          {autoPos.isPending ? "Auto-positioning…" : "Auto"}
        </button>
        <button
          type="button"
          className="text-xs px-2 py-1 rounded border border-wasteland-700 text-wasteland-200 hover:border-wasteland-500"
          onClick={() => inputRef.current?.click()}
        >
          {file ? "Change file" : "Upload PNG"}
        </button>
        <button
          type="button"
          className="text-xs px-2 py-1 rounded bg-rust-500 hover:bg-rust-400 text-wasteland-950 font-medium disabled:bg-rust-700 disabled:text-wasteland-700"
          disabled={!file || upload.isPending}
          onClick={() => upload.mutate()}
        >
          {upload.isPending ? "Writing…" : "Write"}
        </button>
      </div>
      {upload.isError && (
        <div className="w-full text-[11px] text-rust-300">
          Write failed — check the Backups page if anything looks wrong in-game.
        </div>
      )}
      {upload.isSuccess && upload.data && (
        <div className="w-full text-[11px] text-green-300/90">
          ✓ Wrote {upload.data.written.length} file(s)
          {upload.data.backup_id && (
            <span className="text-wasteland-500"> · backup {upload.data.backup_id}</span>
          )}
        </div>
      )}
      {autoPos.isSuccess && autoPos.data && autoPos.data.written[0] && (() => {
        const w = autoPos.data.written[0];
        return (
          <div className="w-full text-[11px] text-blue-300/90">
            ✓ Auto-positioned from source frame {w.source_face_index} (source eye{" "}
            {w.source_eye_xy.join(",")} → your eye {w.target_eye_xy.join(",")}, applied offset{" "}
            {w.applied_offset_xy.join(",")})
            {autoPos.data.backup_id && (
              <span className="text-wasteland-500"> · backup {autoPos.data.backup_id}</span>
            )}
          </div>
        );
      })()}
      {autoPos.isError && (
        <div className="w-full text-[11px] text-rust-300">
          Auto-position failed — check the Backups page if anything looks wrong in-game.
        </div>
      )}

      {/* Offset widget — visible whenever there's a known offset for the
          frame: either from a session mutation (Auto / nudge / setOffset)
          OR baked into the existing frame on disk. The disk-side fallback
          (via current.data?.offset_xy in liveOffset) is what makes the
          widget surface for mercs whose FaceGear was authored in a prior
          session. Out-of-range frames have no offset → liveOffset is
          null → widget hidden, which is correct because nudge/setOffset
          would fail with "face_index >= frame count".

          Two interaction paths share the same underlying offset:
            - ←↑↓→ arrows  → POST /facegear/nudge (±1 px delta)
            - X/Y inputs   → POST /facegear/set-offset (absolute value)
          Both take a backup before every edit. */}
      {liveOffset && (
        <div className="w-full mt-1 flex items-center gap-3 flex-wrap text-[11px] text-blue-200/80">
          <div className="flex items-center gap-2">
            <span className="text-wasteland-500">nudge</span>
            <div className="flex items-center gap-0.5">
              <NudgeButton label="←" onClick={() => nudge.mutate({ dx: -1, dy: 0 })} disabled={nudge.isPending || setOffset.isPending} />
              <NudgeButton label="↑" onClick={() => nudge.mutate({ dx: 0, dy: -1 })} disabled={nudge.isPending || setOffset.isPending} />
              <NudgeButton label="↓" onClick={() => nudge.mutate({ dx: 0, dy: 1 })} disabled={nudge.isPending || setOffset.isPending} />
              <NudgeButton label="→" onClick={() => nudge.mutate({ dx: 1, dy: 0 })} disabled={nudge.isPending || setOffset.isPending} />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <OffsetInput
              axis="X"
              value={liveOffset[0]}
              disabled={nudge.isPending || setOffset.isPending}
              onCommit={(next) => {
                if (next !== liveOffset[0]) {
                  setOffset.mutate({ x: next, y: liveOffset[1] });
                }
              }}
            />
            <OffsetInput
              axis="Y"
              value={liveOffset[1]}
              disabled={nudge.isPending || setOffset.isPending}
              onCommit={(next) => {
                if (next !== liveOffset[1]) {
                  setOffset.mutate({ x: liveOffset[0], y: next });
                }
              }}
            />
          </div>
          {(nudge.isPending || setOffset.isPending) && (
            <span className="text-wasteland-500">writing…</span>
          )}
        </div>
      )}
      {nudge.isError && (
        <div className="w-full text-[11px] text-rust-300">
          Nudge failed — last successful offset preserved. Restore from Backups if needed.
        </div>
      )}
      {setOffset.isError && (
        <div className="w-full text-[11px] text-rust-300">
          Set-offset failed — last successful offset preserved. Restore from Backups if needed.
        </div>
      )}
    </div>
  );
}

/** Single ±1 nudge arrow. Small target — these go in a tight 4-button
 *  cluster so the user can hammer them quickly to fine-tune. */
function NudgeButton({
  label, onClick, disabled,
}: {
  label: string;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="w-6 h-6 rounded border border-blue-500/40 bg-blue-500/10 text-blue-100 hover:bg-blue-500/25 disabled:opacity-40 disabled:cursor-wait font-mono text-sm leading-none flex items-center justify-center"
      title={`Shift offset by 1 ${label === "←" ? "left" : label === "→" ? "right" : label === "↑" ? "up" : "down"}`}
    >
      {label}
    </button>
  );
}

/** Editable X or Y coordinate input. Commits on Enter or blur, only
 *  fires the mutation when the value actually changed. Bound to ±99 —
 *  values beyond that push gear entirely off the 48×43 canvas. The
 *  parent's `liveOffset` re-syncs this when the mutation lands. */
function OffsetInput({
  axis, value, disabled, onCommit,
}: {
  axis: "X" | "Y";
  value: number;
  disabled: boolean;
  onCommit: (next: number) => void;
}) {
  // Local string state so the user can type freely (including a lone
  // "-" character while composing a negative number). Syncs to `value`
  // on every parent re-render so a successful mutation pulls the input
  // back in line with the new live offset.
  const [draft, setDraft] = useState<string>(String(value));
  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  const commit = () => {
    const parsed = parseInt(draft, 10);
    if (Number.isFinite(parsed)) {
      // Clamp to the input's declared range so a typo doesn't push the
      // gear off-canvas; the sidecar's INT16 guard is a separate safety net.
      const clamped = Math.max(-99, Math.min(99, parsed));
      if (clamped !== value) {
        onCommit(clamped);
        return;
      }
    }
    // No-op (NaN or unchanged): restore the input to the canonical value
    // so a partial edit doesn't leave the display out of sync.
    setDraft(String(value));
  };

  return (
    <label className="flex items-center gap-1 font-mono text-blue-200/80">
      <span className="text-wasteland-500">{axis}:</span>
      <input
        type="number"
        inputMode="numeric"
        min={-99}
        max={99}
        step={1}
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            commit();
            (e.target as HTMLInputElement).blur();
          } else if (e.key === "Escape") {
            setDraft(String(value));
            (e.target as HTMLInputElement).blur();
          }
        }}
        className="w-12 text-[11px] px-1.5 py-0.5 rounded border border-blue-500/40 bg-wasteland-900 text-blue-100 font-mono disabled:opacity-50"
        title={`Set absolute sOffset${axis}. Enter / blur to apply. Esc to cancel.`}
      />
    </label>
  );
}
