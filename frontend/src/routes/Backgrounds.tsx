import { useEffect, useMemo, useState } from "react";
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
import BackgroundLibraryBrowser from "../components/BackgroundLibraryBrowser";
import ConfirmModal from "../components/ConfirmModal";
import FieldHelp from "../components/items/FieldHelp";

type Mode = { kind: "none" } | { kind: "create" } | { kind: "edit"; id: number };
type ImpPrompt = { kind: "single"; id: number } | { kind: "all" };

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
  const [confirmImp, setConfirmImp] = useState<ImpPrompt | null>(null);
  const [listQuery, setListQuery] = useState("");         // filter the install's list
  const [libOpen, setLibOpen] = useState(false);          // "Add from Library" drawer
  const [pendingEditId, setPendingEditId] = useState<number | null>(null); // select after import

  const data = bgs.data;
  const schema = data?.schema_fields ?? [];
  const entries = data?.backgrounds ?? [];

  // Capacity: occupied non-template ids vs the engine's editable ceiling (1..499;
  // idx 0 is the template, idx >=500 is silently dropped). "free" is how many more
  // a create/import can add — next_free fills gaps or appends at max+1.
  const cap = data?.max_index ?? 499;
  const usedCount = useMemo(() => entries.filter((e) => e.id !== 0).length, [entries]);
  const free = Math.max(0, cap - usedCount);
  const full = free <= 0;

  const filtered = useMemo(() => {
    const q = listQuery.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter((e) =>
      String(e.id) === q
      || e.name.toLowerCase().includes(q)
      || e.short_name.toLowerCase().includes(q));
  }, [entries, listQuery]);

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

  const openDeletePrompt = (id: number) => {
    deleteMut.reset();
    setConfirmDelete(id);
  };
  const dismissDeletePrompt = () => {
    deleteMut.reset();
    setConfirmDelete(null);
  };
  const openImpPrompt = (id: number) => {
    impMut.reset();
    setConfirmImp({ kind: "single", id });
  };
  const openAllImpPrompt = () => {
    impMut.reset();
    setConfirmImp({ kind: "all" });
  };
  const dismissImpPrompt = () => {
    impMut.reset();
    setConfirmImp(null);
  };

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

  // After "Add from Library" imports an entry, the refetched catalog gains the new
  // id — select + populate the editor for it so the user lands on it ready to wire.
  useEffect(() => {
    if (pendingEditId == null) return;
    const e = entries.find((x) => x.id === pendingEditId);
    if (e) {
      startEdit(e);
      setPendingEditId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, pendingEditId]);

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

  const pct = Math.min(100, Math.round((usedCount / cap) * 100));
  const barColor = free <= 10 ? "bg-rust-500" : free <= 60 ? "bg-amber-500" : "bg-emerald-600";

  return (
    <Shell>
      {/* Capacity meter — how full this install's table is vs the engine's 499 ceiling */}
      <div className="mb-4 rounded border border-wasteland-700 bg-wasteland-900/60 p-3">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-2 text-sm">
          <span className="font-medium text-wasteland-100">
            <span className="font-mono">{usedCount}</span> / <span className="font-mono">{cap}</span> slots used
            <span className="text-wasteland-400"> · {free} free</span>
          </span>
          {full && (
            <span className="badge bg-rust-500/20 text-rust-300">Table full — delete one before adding</span>
          )}
        </div>
        <div className="h-2 w-full rounded-full bg-wasteland-800 overflow-hidden">
          <div className={`h-full ${barColor} transition-all`} style={{ width: `${pct}%` }} />
        </div>
        <p className="mt-1 text-[10px] text-wasteland-500">
          The engine caps the table at {cap + 1} (idx 0 = template, idx ≥ {cap + 1} silently dropped).
        </p>
      </div>

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
              onClick={openAllImpPrompt}
            >
              Make all IMP-selectable
            </button>
          )}
        </div>
      </div>

      {/* Toolbar: search the install's list + the two ways to add a background */}
      <div className="mb-4 flex items-center gap-2 flex-wrap">
        <input
          className="input flex-1 min-w-[12rem]"
          placeholder="Search this install's backgrounds (name or id)…"
          value={listQuery}
          onChange={(e) => setListQuery(e.target.value)}
        />
        <button
          className="btn-secondary"
          disabled={full}
          title={full ? "Table full — delete one first" : "Create a blank background"}
          onClick={startCreate}
        >
          + Create blank
        </button>
        <button
          className="btn-primary"
          disabled={full}
          title={full ? "Table full — delete one first" : "Browse the cross-mod library and import one"}
          onClick={() => setLibOpen(true)}
        >
          + Add from Library
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[20rem_1fr] gap-6">
        {/* List */}
        <section className="card p-0 overflow-hidden self-start">
          <div className="flex items-center justify-between p-3 border-b border-wasteland-700">
            <h2 className="text-sm font-semibold">
              {filtered.length === entries.length
                ? `${entries.length} backgrounds`
                : `${filtered.length} of ${entries.length}`}
            </h2>
          </div>
          <ul className="max-h-[70vh] overflow-y-auto divide-y divide-wasteland-800">
            {filtered.map((e) => (
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
            {filtered.length === 0 && (
              <li className="px-3 py-6 text-center text-xs text-wasteland-500">No backgrounds match "{listQuery}".</li>
            )}
          </ul>
        </section>

        {/* Editor */}
        <section className="min-w-0">
          {mode.kind === "none" && (
            <div className="card text-sm text-wasteland-400">
              Pick a background to edit, create a blank one, or <button type="button" className="text-rust-400 hover:underline" onClick={() => !full && setLibOpen(true)}>add one from the library</button>.
              Changes write to this install's <code className="font-mono">Backgrounds.xml</code>.
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
                        onClick={() => openImpPrompt(mode.id)}
                      >
                        Make IMP-selectable
                      </button>
                    )}
                    <button
                      className="btn-ghost text-xs text-rust-400"
                      onClick={() => openDeletePrompt(mode.id)}
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>

              {mode.kind === "create" && (
                <div className="card grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-xs text-wasteland-300 flex items-center gap-1">
                      ID (blank = next free, max {data.max_index})
                      <FieldHelp help="Engine array index 1-499. Leave blank to take the next free id. 0 is the template row; ids 500+ are [[clamp|silently dropped]] on load. Shown in [[IMP]] if at or before the last physical entry." />
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

      {/* Add-from-Library slide-over drawer */}
      {libOpen && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm"
          onClick={() => setLibOpen(false)}
        >
          <div
            className="h-full w-full max-w-5xl bg-wasteland-900 border-l border-wasteland-700 shadow-2xl overflow-y-auto p-4"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-base font-semibold">Add from Library</h3>
                <p className="text-xs text-wasteland-400">
                  {free} free slot{free === 1 ? "" : "s"} — import renumbers the block to the lowest free id and selects it here to edit/wire.
                </p>
              </div>
              <button type="button" className="btn-ghost text-sm" onClick={() => setLibOpen(false)}>Close</button>
            </div>
            <BackgroundLibraryBrowser
              onAssigned={(id) => {
                invalidate();
                setPendingEditId(id);   // effect selects + populates the editor once refetched
                setLibOpen(false);
              }}
            />
          </div>
        </div>
      )}

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
        busy={deleteMut.isPending}
        error={deleteMut.isError
          ? "The background could not be deleted. Try again or cancel."
          : null}
        onConfirm={() => confirmDelete !== null && deleteMut.mutate(confirmDelete)}
        onCancel={dismissDeletePrompt}
      />

      <ConfirmModal
        open={confirmImp !== null}
        title={confirmImp?.kind === "all"
          ? "Make all backgrounds selectable in IMP?"
          : "Make selectable in IMP?"}
        body={
          <>
            {confirmImp?.kind === "all"
              ? "This changes every currently hidden background so IMP character creation lists them. A backup is taken automatically."
              : "This moves the background to the end of the file so IMP character creation lists it — which also reveals every background with a lower id. A backup is taken automatically."}
          </>
        }
        confirmLabel={confirmImp?.kind === "all" ? "Make all selectable" : "Make selectable"}
        busy={impMut.isPending}
        error={impMut.isError
          ? "The background visibility change could not be completed. Try again or cancel."
          : null}
        onConfirm={() => {
          if (confirmImp?.kind === "all") impMut.mutate({ all: true });
          if (confirmImp?.kind === "single") impMut.mutate({ ui_index: confirmImp.id });
        }}
        onCancel={dismissImpPrompt}
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
            Manage this install's stat/AP/perk bundles — edit, create, or add from the cross-mod library.
          </p>
        </div>
        <Link to="/" className="btn-ghost text-sm">← Hub</Link>
      </div>
      {children}
    </div>
  );
}
