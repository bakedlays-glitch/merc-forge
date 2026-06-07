import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createBackground,
  deleteBackground,
  formatApiError,
  listBackgrounds,
  setBackgroundImpThreshold,
  updateBackground,
  type BackgroundClamp,
  type BackgroundEntry,
} from "../lib/api";
import BackgroundForm, { type BackgroundDraft } from "../components/forms/BackgroundForm";
import ConfirmModal from "../components/ConfirmModal";

type Mode = { kind: "none" } | { kind: "create" } | { kind: "edit"; id: number };

const EMPTY_DRAFT: BackgroundDraft = { name: "", short_name: "", description: "", fields: {} };

export default function Backgrounds() {
  const qc = useQueryClient();
  const bgs = useQuery({ queryKey: ["backgrounds"], queryFn: () => listBackgrounds() });

  const [mode, setMode] = useState<Mode>({ kind: "none" });
  const [draft, setDraft] = useState<BackgroundDraft>(EMPTY_DRAFT);
  const [manualId, setManualId] = useState("");          // create: blank = auto
  const [makeImp, setMakeImp] = useState(false);          // create: IMP-selectable
  const [clamps, setClamps] = useState<BackgroundClamp[] | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [confirmImp, setConfirmImp] = useState<number | null>(null);

  const data = bgs.data;
  const schema = data?.schema_fields ?? [];
  const entries = data?.backgrounds ?? [];

  // Build a full owned-field payload (all schema keys, defaulting 0) so a PUT
  // fully syncs the owned columns (zeroed fields get removed server-side).
  const fullFields = (d: BackgroundDraft) =>
    Object.fromEntries(schema.map((s) => [s.key, d.fields[s.key] ?? 0]));

  const invalidate = () => qc.invalidateQueries({ queryKey: ["backgrounds"] });

  const createMut = useMutation({
    mutationFn: () =>
      createBackground({
        name: draft.name,
        short_name: draft.short_name,
        description: draft.description,
        fields: fullFields(draft),
        ui_index: manualId.trim() === "" ? null : Number(manualId),
        make_imp_selectable: makeImp,
      }),
    onSuccess: (res) => {
      invalidate();
      setClamps(res.clamps ?? []);
      setMode({ kind: "edit", id: res.ui_index! });
    },
  });

  const updateMut = useMutation({
    mutationFn: (id: number) =>
      updateBackground(id, {
        name: draft.name,
        short_name: draft.short_name,
        description: draft.description,
        fields: fullFields(draft),
      }),
    onSuccess: (res) => {
      invalidate();
      setClamps(res.clamps ?? []);
    },
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteBackground(id),
    onSuccess: () => {
      invalidate();
      setConfirmDelete(null);
      setMode({ kind: "none" });
    },
  });

  const impMut = useMutation({
    mutationFn: (body: { ui_index?: number; all?: boolean }) => setBackgroundImpThreshold(body),
    onSuccess: () => {
      invalidate();
      setConfirmImp(null);
    },
  });

  const startCreate = () => {
    setDraft(EMPTY_DRAFT);
    setManualId("");
    setMakeImp(false);
    setClamps(null);
    setMode({ kind: "create" });
  };

  const startEdit = (e: BackgroundEntry) => {
    const fields: Record<string, number> = {};
    for (const m of e.modifiers) fields[m.key] = m.value; // known keys land in the form
    setDraft({ name: e.name, short_name: e.short_name, description: e.description, fields });
    setClamps(null);
    setMode({ kind: "edit", id: e.id });
  };

  const hiddenFromImp = useMemo(
    () => entries.filter((e) => e.id !== 0 && !e.imp_selectable),
    [entries],
  );
  const editingEntry =
    mode.kind === "edit" ? entries.find((e) => e.id === mode.id) : undefined;
  const saving = createMut.isPending || updateMut.isPending;
  const saveErr = createMut.error || updateMut.error;

  // ── Empty / error states ──────────────────────────────────────────────────
  if (bgs.isLoading) {
    return <Shell><p className="text-sm text-wasteland-400">Loading backgrounds…</p></Shell>;
  }
  if (bgs.isError || !data) {
    return (
      <Shell>
        <p className="text-sm text-rust-400">{formatApiError(bgs.error)}</p>
      </Shell>
    );
  }
  if (!data.file_present || !data.writable) {
    return (
      <Shell>
        <div className="card">
          <p className="text-sm text-wasteland-200">
            This install has no <code className="font-mono">TableData/Backgrounds.xml</code>,
            so there's no background table to edit. (Pre-STOMP mods don't ship one.)
          </p>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      {data.duplicate_ids.length > 0 && (
        <div className="mb-4 rounded border border-rust-700 bg-rust-950/40 p-3 text-xs text-rust-200">
          ⚠ Backgrounds.xml has duplicate uiIndex values: {data.duplicate_ids.join(", ")}.
          The engine uses the last of each; editing a duplicated id is blocked until you
          fix the file by hand.
        </div>
      )}

      {/* IMP-visibility banner */}
      <div className="mb-4 rounded border border-wasteland-700 bg-wasteland-900/60 p-3 text-xs text-wasteland-200">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <span>
            IMP character creation lists backgrounds <span className="font-mono">0–{data.num_found_background}</span>.
            {hiddenFromImp.length > 0 && (
              <> {hiddenFromImp.length} higher-id background{hiddenFromImp.length === 1 ? "" : "s"} exist
              but won't appear there (they still work when assigned to a merc).</>
            )}
          </span>
          {hiddenFromImp.length > 0 && (
            <button
              type="button"
              className="btn-ghost text-xs shrink-0"
              disabled={impMut.isPending}
              onClick={() => impMut.mutate({ all: true })}
            >
              {impMut.isPending ? "Working…" : "Make all IMP-selectable"}
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[20rem_1fr] gap-6">
        {/* List */}
        <section className="card p-0 overflow-hidden self-start">
          <div className="flex items-center justify-between p-3 border-b border-wasteland-700">
            <h2 className="text-sm font-semibold">{entries.length} backgrounds</h2>
            <button className="btn-primary text-xs" onClick={startCreate}>+ New</button>
          </div>
          <ul className="max-h-[70vh] overflow-y-auto divide-y divide-wasteland-800">
            {entries.map((e) => (
              <li key={e.id}>
                <button
                  type="button"
                  onClick={() => startEdit(e)}
                  className={`w-full text-left px-3 py-2 hover:bg-wasteland-800/60 ${
                    mode.kind === "edit" && mode.id === e.id ? "bg-wasteland-800" : ""
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm truncate">
                      <span className="font-mono text-xs text-wasteland-500">{e.id}</span>{" "}
                      {e.id === 0 ? <span className="text-wasteland-500">(template)</span>
                        : (e.short_name || e.name || `#${e.id}`)}
                    </span>
                    <span className="flex items-center gap-1 shrink-0">
                      {e.has_advanced_data && (
                        <span className="badge bg-wasteland-700 text-wasteland-300" title="Has drug/extra data preserved on save">adv</span>
                      )}
                      {!e.imp_selectable && e.id !== 0 && (
                        <span className="badge bg-amber-900/50 text-amber-300" title="Not shown in IMP character creation">no-IMP</span>
                      )}
                    </span>
                  </div>
                  {e.modifiers.length > 0 && (
                    <div className="text-[10px] text-wasteland-500 font-mono truncate">
                      {e.modifiers.length} modifier{e.modifiers.length === 1 ? "" : "s"}
                    </div>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>

        {/* Editor */}
        <section className="min-w-0">
          {mode.kind === "none" && (
            <div className="card text-sm text-wasteland-400">
              Pick a background to edit, or create a new one. Changes write to this
              install's <code className="font-mono">Backgrounds.xml</code>.
            </div>
          )}

          {(mode.kind === "create" || mode.kind === "edit") && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold">
                  {mode.kind === "create" ? "New background"
                    : `Edit #${mode.id}: ${editingEntry?.short_name || editingEntry?.name || ""}`}
                </h2>
                {mode.kind === "edit" && (
                  <div className="flex items-center gap-2">
                    {editingEntry && !editingEntry.imp_selectable && editingEntry.id !== 0 && (
                      <button
                        className="btn-ghost text-xs text-amber-400"
                        title="Move this background last so IMP creation lists it"
                        onClick={() => setConfirmImp(mode.id)}
                      >
                        Make IMP-selectable
                      </button>
                    )}
                    <button
                      className="btn-ghost text-xs text-rust-400"
                      onClick={() => setConfirmDelete(mode.id)}
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>

              {mode.kind === "create" && (
                <div className="card grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-xs text-wasteland-300">
                      ID (blank = next free, max {data.max_index})
                    </span>
                    <input
                      className="input mt-1 font-mono"
                      placeholder="auto"
                      value={manualId}
                      onChange={(e) => setManualId(e.target.value.replace(/[^0-9]/g, ""))}
                    />
                  </label>
                  <label className="flex items-start gap-2 mt-1">
                    <input
                      type="checkbox"
                      className="mt-1 accent-rust-500 h-4 w-4"
                      checked={makeImp}
                      onChange={(e) => setMakeImp(e.target.checked)}
                    />
                    <span className="text-xs text-wasteland-300">
                      Make selectable in IMP character creation
                      <span className="block text-[10px] text-wasteland-500">
                        Places it last in the file. Also reveals any currently-hidden
                        higher-id backgrounds in the IMP list.
                      </span>
                    </span>
                  </label>
                </div>
              )}

              {editingEntry?.has_advanced_data && (
                <div className="rounded border border-wasteland-700 bg-wasteland-900/40 p-2 text-[11px] text-wasteland-400">
                  This background has drug-list or other advanced data not shown below.
                  It's preserved exactly when you save.
                </div>
              )}

              <BackgroundForm
                schema={schema}
                draft={draft}
                onChange={(patch) => setDraft((d) => ({ ...d, ...patch }))}
                caps={{ name: data.name_max, short: data.short_name_max, description: data.description_max }}
              />

              {clamps && clamps.length > 0 && (
                <div className="rounded border border-amber-700 bg-amber-950/30 p-2 text-[11px] text-amber-200">
                  Saved. Some values were adjusted to the engine's range:
                  {clamps.map((c) => (
                    <span key={c.key} className="block font-mono">
                      {c.key}: {c.requested} → {c.stored}
                    </span>
                  ))}
                </div>
              )}
              {clamps && clamps.length === 0 && (
                <div className="rounded border border-emerald-800 bg-emerald-950/30 p-2 text-[11px] text-emerald-200">
                  Saved.
                </div>
              )}
              {saveErr && (
                <div className="text-sm text-rust-400">{formatApiError(saveErr)}</div>
              )}

              <div className="flex items-center gap-2">
                <button
                  className="btn-primary"
                  disabled={saving || !draft.name.trim()}
                  onClick={() =>
                    mode.kind === "create" ? createMut.mutate() : updateMut.mutate(mode.id)
                  }
                >
                  {saving ? "Saving…" : mode.kind === "create" ? "Create" : "Save changes"}
                </button>
                <button className="btn-ghost" onClick={() => { setMode({ kind: "none" }); setClamps(null); }}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </section>
      </div>

      <ConfirmModal
        open={confirmDelete !== null}
        title="Delete background?"
        body={
          <>
            This removes the background from <code className="font-mono">Backgrounds.xml</code> for
            every merc in this install. Any merc currently assigned to it will fall back to
            "no background". A backup is taken automatically.
          </>
        }
        confirmLabel="Delete"
        destructive
        onConfirm={() => confirmDelete !== null && deleteMut.mutate(confirmDelete)}
        onCancel={() => setConfirmDelete(null)}
      />

      <ConfirmModal
        open={confirmImp !== null}
        title="Make selectable in IMP?"
        body={
          <>
            This moves the background to the end of the file so IMP character creation
            lists it — which also reveals every background with a lower id. A backup is
            taken automatically.
          </>
        }
        confirmLabel="Make selectable"
        onConfirm={() => confirmImp !== null && impMut.mutate({ ui_index: confirmImp })}
        onCancel={() => setConfirmImp(null)}
      />
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Backgrounds</h1>
          <p className="text-sm text-wasteland-300 mt-1">
            Create, edit, and delete the stat/AP/perk bundles mercs can carry.
          </p>
        </div>
        <Link to="/" className="btn-ghost text-sm">← Hub</Link>
      </div>
      {children}
    </div>
  );
}
