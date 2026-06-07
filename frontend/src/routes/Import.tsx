import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  extractAuditIssues,
  formatApiError,
  getRoster,
  importBundle,
  importPreview,
} from "../lib/api";
import { pickFile } from "../lib/tauri";
import { SlotLockWarningModal } from "../components/SlotLockWarningModal";
import { useSlotLockGuard } from "../lib/slotLocks";
import { useSlotPicker, type SlotInfo } from "../lib/slotPicker";

/** Engine-faithful Type/slot heads-up. Uses live AIM/MERC row presence from
 * the slot picker rather than the legacy hardcoded slot ranges — a Type=1
 * bundle landing at slot 220 in The Wasteland (which has an AIM row past
 * 199) is fine, but the old static check would have warned anyway. */
function slotTypeWarning(slot: number, type: number, info: SlotInfo | undefined): string | null {
  if (info === undefined) return null;
  if (type === 1 && !info.aim_row.present) {
    return `This merc is marked for the AIM website, but slot ${slot} has no AIM listing right now. If the bundle includes AIM info, MercForge adds the listing on import and the merc shows up on the AIM hiring page. If it doesn't, you'll need to add the listing for this slot yourself, or the merc won't appear on AIM.`;
  }
  if (type === 2 && !info.merc_row.present) {
    return `This merc is set up for Speck's M.E.R.C. service, but slot ${slot} isn't listed there yet. MercForge will add it during import, so the merc shows up on the M.E.R.C. website after you save.`;
  }
  if (type === 1 && info.merc_row.present && !info.aim_row.present) {
    return `Slot ${slot} currently has a M.E.R.C. row but no AIM row. Importing Type=AIM here adds an AIM row alongside it; the merc may appear on both hire lists until the M.E.R.C. row is removed.`;
  }
  if (type === 2 && info.aim_row.present && !info.merc_row.present) {
    return `Slot ${slot} currently has an AIM row but no M.E.R.C. row. Importing Type=MERC here adds a M.E.R.C. row alongside it; the merc may appear on both hire lists until the AIM row is removed.`;
  }
  return null;
}

export default function Import() {
  const qc = useQueryClient();
  const lockGuard = useSlotLockGuard();
  const picker = useSlotPicker();
  // Honor ?slot= when the Roster sends us here with the target already chosen.
  const [params] = useSearchParams();
  const initialSlot = params.get("slot");
  const [bundlePath, setBundlePath] = useState<string | null>(null);
  const [targetSlot, setTargetSlot] = useState<number | null>(
    initialSlot !== null && /^\d+$/.test(initialSlot) ? Number(initialSlot) : null
  );
  // When the URL chose the slot for us, treat it as "user-touched" so the
  // bundle-preview effect below doesn't blow it away with the manifest's slot.
  const [force, setForce] = useState(false);
  // Track that the user edited target slot manually so we don't override it
  // when the preview re-runs. Keyed by bundle path — re-picking the SAME
  // path doesn't reset the user's manual slot edit. Pre-fix: any time
  // useQuery refetched (StrictMode remount, focus refetch, etc.) the
  // effect below would auto-reset targetSlot back to the manifest value,
  // surprising users who had picked a different destination.
  const [touchedForPath, setTouchedForPath] = useState<string | null>(null);
  const slotTouched = touchedForPath === bundlePath;

  const preview = useQuery({
    queryKey: ["importPreview", bundlePath],
    queryFn: () => importPreview(bundlePath!),
    enabled: bundlePath !== null,
    retry: false,
  });

  // Default target slot to the manifest's slot the first time a bundle loads.
  // Skipped entirely when ?slot= pre-set the target — the URL is the user's
  // explicit intent and shouldn't get overwritten by the bundle's manifest.
  const urlInitialSlot = initialSlot !== null && /^\d+$/.test(initialSlot);
  useEffect(() => {
    if (preview.data && !slotTouched && !urlInitialSlot) {
      setTargetSlot(preview.data.manifest.merc.uiIndex);
    }
  }, [preview.data, slotTouched, urlInitialSlot]);

  const roster = useQuery({ queryKey: ["roster"], queryFn: () => getRoster() });
  const targetEntry = targetSlot !== null
    ? roster.data?.find((e) => e.slot === targetSlot)
    : undefined;
  const slotOccupied = !!targetEntry && !targetEntry.is_empty;

  const importMut = useMutation({
    mutationFn: () => importBundle(bundlePath!, targetSlot ?? undefined, force),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["roster"] });
      // Slot picker — bug-review finding E4.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
    },
  });

  const onPickFile = async () => {
    const path = await pickFile("Pick a .wmerc bundle", [
      { name: "Merc Forge bundle", extensions: ["wmerc"] },
    ]);
    if (path) {
      setBundlePath(path);
      // touchedForPath stays tied to the previous bundle path; once the
      // user picks a new bundle the path-key comparison auto-resets
      // slotTouched. If they re-pick the SAME path, their manual slot
      // edit is preserved.
      setForce(false);
      importMut.reset();
    }
  };

  const m = preview.data?.manifest;
  const auditIssues = importMut.isError ? extractAuditIssues(importMut.error) : [];
  const targetSlotInfo = targetSlot !== null ? picker.data?.slots[targetSlot] : undefined;
  const typeWarning =
    m && targetSlot !== null ? slotTypeWarning(targetSlot, m.merc.Type, targetSlotInfo) : null;

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Import .wmerc bundle</h1>
        <Link to="/" className="btn-ghost text-sm">← Back to Hub</Link>
      </div>

      <section className="card">
        <h2 className="text-lg font-semibold mb-3">Step 1: Pick the bundle file</h2>
        <div className="flex items-center gap-3">
          <button className="btn-primary" onClick={onPickFile}>
            Choose .wmerc file...
          </button>
          {bundlePath && (
            <span className="text-xs text-wasteland-300 font-mono truncate" title={bundlePath}>
              {bundlePath}
            </span>
          )}
        </div>
      </section>

      {bundlePath && preview.isLoading && (
        <div className="card text-wasteland-300">Reading bundle...</div>
      )}

      {bundlePath && preview.isError && (
        <div className="card text-rust-400">
          Couldn't read this bundle: {formatApiError(preview.error)}
        </div>
      )}

      {m && (
        <>
          <section className="card space-y-2">
            <h2 className="text-lg font-semibold">Step 2: Preview the merc</h2>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-wasteland-400 text-xs uppercase">Name</div>
                <div className="font-medium">{m.merc.zName} <span className="text-wasteland-400">({m.merc.zNickname})</span></div>
              </div>
              <div>
                <div className="text-wasteland-400 text-xs uppercase">Source slot</div>
                <div className="font-mono">{m.merc.uiIndex} <span className="text-wasteland-400 text-xs">(face {m.merc.ubFaceIndex})</span></div>
              </div>
              <div>
                <div className="text-wasteland-400 text-xs uppercase">Type</div>
                <div>{m.merc.Type === 1 ? "AIM" : m.merc.Type === 2 ? "MERC" : m.merc.Type === 3 ? "RPC" : `Type ${m.merc.Type}`}</div>
              </div>
              {m.compat.intended_mod !== "any" && (
                <div>
                  <div className="text-wasteland-400 text-xs uppercase">Intended mod</div>
                  <div>{m.compat.intended_mod}</div>
                </div>
              )}
              {m.author.name && (
                <div>
                  <div className="text-wasteland-400 text-xs uppercase">Author</div>
                  <div>{m.author.name}</div>
                </div>
              )}
              {m.license && m.license !== "unspecified" && (
                <div>
                  <div className="text-wasteland-400 text-xs uppercase">License</div>
                  <div>{m.license}</div>
                </div>
              )}
            </div>
            {m.notes && (
              <div className="text-sm pt-2 border-t border-wasteland-700">
                <div className="text-wasteland-400 text-xs uppercase mb-1">Notes</div>
                <div className="whitespace-pre-wrap">{m.notes}</div>
              </div>
            )}
            <div className="flex flex-wrap gap-2 pt-2 border-t border-wasteland-700">
              {preview.data?.has_portrait && <span className="badge bg-rust-500/20 text-rust-400">Portrait PNGs</span>}
              {preview.data?.has_voice && (
                <span className="badge bg-rust-500/20 text-rust-400">
                  {m.voice?.count ?? 0} voice clip{(m.voice?.count ?? 0) === 1 ? "" : "s"}
                </span>
              )}
              {m.aim_binding && <span className="badge bg-wasteland-700 text-wasteland-200">AIM binding</span>}
              {m.gear.length > 0 && <span className="badge bg-wasteland-700 text-wasteland-200">{m.gear.length} gear kit{m.gear.length === 1 ? "" : "s"}</span>}
            </div>
          </section>

          <section className="card space-y-3">
            <h2 className="text-lg font-semibold">Step 3: Pick the target slot</h2>
            <div className="flex items-center gap-3">
              <input
                type="number"
                className="input max-w-[8rem]"
                min={0}
                max={255}
                value={targetSlot ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  setTargetSlot(v === "" ? null : Number(v));
                  setTouchedForPath(bundlePath);
                }}
              />
              <span className="text-sm text-wasteland-400">
                {targetSlot !== null && !targetEntry && "loading..."}
                {targetEntry && targetEntry.is_empty && "Empty slot — safe to write."}
                {targetEntry && !targetEntry.is_empty && (
                  <span className="text-rust-400">
                    Occupied by {targetEntry.nickname ?? targetEntry.name ?? `slot ${targetSlot}`}.
                  </span>
                )}
              </span>
            </div>
            {slotOccupied && (
              <label className="flex items-center gap-2 text-sm text-wasteland-200">
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
                Overwrite the existing merc at slot {targetSlot}
              </label>
            )}
            {typeWarning && (
              <div className="rounded border border-yellow-500/40 bg-yellow-500/10 p-2 text-xs text-yellow-200">
                {typeWarning}
              </div>
            )}
          </section>

          <section className="card space-y-3">
            <h2 className="text-lg font-semibold">Step 4: Import</h2>
            <button
              className="btn-primary"
              // Disable on isSuccess too so a second click doesn't re-run
              // the entire import (would now hit SLOT_OCCUPIED and double-
              // backup). User can pick a new slot if they want another copy.
              disabled={
                targetSlot === null ||
                importMut.isPending ||
                importMut.isSuccess ||
                (slotOccupied && !force)
              }
              onClick={() => targetSlot !== null && lockGuard.guard(targetSlot, () => importMut.mutate())}
            >
              {importMut.isPending
                ? "Importing..."
                : importMut.isSuccess
                  ? `Imported to slot ${targetSlot} ✓`
                  : `Import to slot ${targetSlot ?? "?"}`}
            </button>

            {importMut.isError && (
              <div className="rounded border border-rust-500/40 bg-rust-500/10 p-3 text-sm text-rust-200">
                <div className="font-semibold mb-1">Import failed</div>
                <div>{formatApiError(importMut.error)}</div>
                {auditIssues.length > 0 && (
                  <ul className="mt-2 list-disc list-inside space-y-0.5 text-xs">
                    {auditIssues.map((iss, i) => (
                      <li key={i}>
                        <span className="font-mono">[{iss.severity}]</span> {iss.code}: {iss.message}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {importMut.isSuccess && (() => {
              // Bug-review #93: when partial_failures is non-empty, the
              // import wrote SOME but not ALL the bundle's files (usually
              // one or two voice clips locked by another process,
              // npc_speech audio, or mod-extra XML rows). Pre-fix the
              // banner was green ✓ regardless, and the warning list sat
              // under it where it was easy to miss. Now: the outer
              // banner color/headline reflects whether anything was
              // skipped. Roster + Hub links stay either way so the user
              // can navigate normally.
              const r = importMut.data.report;
              const hasFailures = r.partial_failures.length > 0;
              const outerCls = hasFailures
                ? "rounded border border-yellow-500/60 bg-yellow-500/10 p-3 text-sm space-y-2"
                : "rounded border border-green-500/40 bg-green-500/10 p-3 text-sm space-y-2";
              const headlineCls = hasFailures ? "text-yellow-200" : "text-green-300";
              return (
                <div className={outerCls}>
                  <div className={`font-semibold ${headlineCls}`}>
                    {hasFailures ? "⚠ " : "✓ "}
                    Imported {m.merc.zName} to slot {r.target_slot}
                    {hasFailures && " — some files were skipped"}
                  </div>
                  <div className="text-wasteland-300 text-xs">
                    {r.portrait_compiled && "Portrait STIs compiled. "}
                    {r.voice_clips_copied > 0 && `${r.voice_clips_copied} voice clip(s) copied. `}
                    {r.aim_bio_id_used !== null && `Bio routed via AimBioID ${r.aim_bio_id_used}.`}
                  </div>
                  {hasFailures && (
                    <div className="rounded border border-yellow-500/60 bg-yellow-950/40 p-2 text-xs text-yellow-100">
                      <div className="font-semibold mb-1">
                        {r.partial_failures.length} file{r.partial_failures.length === 1 ? "" : "s"} couldn't be written:
                      </div>
                      <ul className="list-disc list-inside space-y-0.5 font-mono text-[11px]">
                        {r.partial_failures.map((w, i) => (
                          <li key={i}>{w}</li>
                        ))}
                      </ul>
                      <div className="mt-2 text-[11px] text-yellow-200/80 font-sans">
                        The merc IS hireable — profile, AIM/MERC row, and bio all wrote successfully.
                        The skipped files are usually voice clips locked by another process or mod
                        extras the target install doesn't have a path for. Retry the import after
                        closing the offending app (audio editor, Explorer preview), or copy the
                        missing files manually from the source bundle.
                      </div>
                    </div>
                  )}
                  <div className="flex gap-2 pt-1">
                    <Link to="/merc-wizard" className="btn-ghost text-xs">View roster</Link>
                    <Link to="/" className="btn-ghost text-xs">Back to Hub</Link>
                  </div>
                </div>
              );
            })()}
          </section>
        </>
      )}
      {lockGuard.pending && (
        <SlotLockWarningModal
          lock={lockGuard.pending.lock}
          action="import"
          onConfirm={lockGuard.confirm}
          onCancel={lockGuard.cancel}
        />
      )}
    </div>
  );
}
