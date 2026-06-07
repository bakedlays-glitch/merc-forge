/**
 * JSD viewer + editor for the Tileset Editor.
 *
 * View mode: surfaces parsed JSD fields (header + footprint tiles +
 * 5x5 Z-profile grids per tile).
 *
 * Edit mode: lets the user patch field by field. Backend writes ONLY
 * the requested byte spans; everything outside the surfaced subset
 * stays byte-identical. See `sidecar/routes/mapforge.py::update_sti_jsd`
 * for the write logic.
 *
 * Scope per `docs/TILESET_EDITOR_SPLIT.md` — full editor: flags,
 * Armour/HP/Density/offsets, per-tile bXPos/bYPos/sPos, 5x5 profile
 * grids. ubNumberOfTiles is intentionally read-only (resizing the file
 * is out of scope for this arc).
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { formatApiError } from "../lib/api";
import {
  getStiJsd,
  updateStiJsd,
  type JsdEditBody,
  type JsdParsed,
  type JsdProfileTile,
} from "../lib/mapforge";

// Flag bit definitions — mirrors _JSD_FLAG_LABELS in mapforge.py. Used
// to surface the fflags UINT16 as a checklist of named bits. If the
// backend table grows new bits, this will fall back to numeric display
// of the remainder.
const JSD_FLAG_BITS: { bit: number; name: string }[] = [
  // The exact bit-to-name mapping is owned by the backend; we mirror
  // here for the *editor* UI so toggling a chip can compute the new
  // UINT16. Worth keeping a tight set — the lesser-used bits stay
  // editable via the raw numeric field below.
  { bit: 0x0001, name: "TILE_ON_ROOF" },
  { bit: 0x0002, name: "HAS_SHADOW_BUDDY" },
  { bit: 0x0004, name: "DAMAGED" },
  { bit: 0x0008, name: "EXPLOSIVE" },
  { bit: 0x0010, name: "PARTIAL_WALL" },
  { bit: 0x0020, name: "FULL_WALL" },
  { bit: 0x0040, name: "WIREFRAME" },
  { bit: 0x0080, name: "PASSABLE" },
  { bit: 0x0100, name: "EXIT_GRID" },
  { bit: 0x0200, name: "BLOCKS_LOS" },
  { bit: 0x0400, name: "OBSTACLE" },
  { bit: 0x0800, name: "SLIDING_DOOR" },
  { bit: 0x1000, name: "DOOR" },
  { bit: 0x2000, name: "OPENABLE" },
  { bit: 0x4000, name: "SEETHROUGH" },
  { bit: 0x8000, name: "BURNABLE" },
];

export function TilesetEditorJsdPanel({
  xmlPath, tileset, slot,
}: {
  xmlPath: string;
  tileset: number;
  slot: number;
}) {
  const qc = useQueryClient();
  const jsd = useQuery({
    queryKey: ["mapforge", "sti-jsd", xmlPath, tileset, slot],
    queryFn: () => getStiJsd(xmlPath, tileset, slot),
    enabled: !!xmlPath && tileset >= 0 && slot >= 0,
    staleTime: 30 * 1000,
    retry: false,
  });
  const [editing, setEditing] = useState(false);
  // The draft is initialized from the parsed JSD on entry to edit mode
  // and resets when the user cancels or after a successful save.
  const [draft, setDraft] = useState<JsdDraft | null>(null);
  // Last-write status — short success banner that fades after 3s.
  const [savedAt, setSavedAt] = useState<number | null>(null);
  useEffect(() => {
    if (savedAt === null) return;
    const t = window.setTimeout(() => setSavedAt(null), 3000);
    return () => window.clearTimeout(t);
  }, [savedAt]);

  const update = useMutation({
    mutationFn: (body: JsdEditBody) => updateStiJsd(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mapforge", "sti-jsd", xmlPath, tileset, slot] });
      setEditing(false);
      setDraft(null);
      setSavedAt(Date.now());
    },
  });

  if (jsd.isLoading) {
    return (
      <div className="rounded border border-gray-800 p-2 text-xs text-gray-500">
        Loading JSD…
      </div>
    );
  }
  if (jsd.error) {
    return (
      <div className="rounded border border-amber-800 p-2 text-xs text-amber-300">
        No JSD: {formatApiError(jsd.error)}
      </div>
    );
  }
  if (!jsd.data) return null;
  const parsed = jsd.data;

  return (
    <div className="rounded border border-gray-800 bg-gray-900/40 p-2 text-xs">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <h4 className="font-semibold text-gray-200">JSD</h4>
        <div className="flex items-center gap-2 text-[10px]">
          {savedAt && (
            <span className="rounded bg-emerald-950/60 px-1.5 py-0.5 text-emerald-200">
              ✓ Saved
            </span>
          )}
          {!editing ? (
            <button
              type="button"
              onClick={() => {
                setDraft(draftFromParsed(parsed));
                setEditing(true);
              }}
              className="rounded border border-gray-700 px-2 py-0.5 text-gray-300 hover:border-gray-500"
            >
              Edit
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => {
                  setDraft(null);
                  setEditing(false);
                }}
                disabled={update.isPending}
                className="rounded border border-gray-700 px-2 py-0.5 text-gray-300 hover:border-gray-500 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={update.isPending || !draft}
                onClick={() => {
                  if (!draft) return;
                  const body = bodyFromDraft({
                    xml: xmlPath, tileset, slot, draft, original: parsed,
                  });
                  update.mutate(body);
                }}
                className="rounded border border-emerald-600 bg-emerald-700 px-2 py-0.5 text-emerald-50 hover:bg-emerald-600 disabled:opacity-50"
              >
                {update.isPending ? "Saving…" : "Save"}
              </button>
            </>
          )}
        </div>
      </div>
      <div className="mb-2 truncate font-mono text-[10px] text-gray-500" title={parsed.jsd_path}>
        {parsed.jsd_path}
      </div>

      {update.error && (
        <p className="mb-2 rounded bg-red-950/60 px-2 py-1 text-[11px] text-red-300">
          {formatApiError(update.error)}
        </p>
      )}

      {/* Header fields */}
      <HeaderSection
        parsed={parsed}
        editing={editing}
        draft={draft}
        setDraft={setDraft}
      />

      {/* Footprint tiles */}
      <details open className="mt-2">
        <summary className="cursor-pointer text-gray-300">
          Footprint tiles ({parsed.tiles.length})
        </summary>
        <div className="mt-1 space-y-1.5">
          {parsed.tiles.map((tile, i) => (
            <TileRow
              key={i}
              tile={tile}
              index={i}
              editing={editing}
              draft={draft}
              setDraft={setDraft}
            />
          ))}
        </div>
      </details>
    </div>
  );
}

// ─── Header section ──────────────────────────────────────────────────

function HeaderSection({
  parsed, editing, draft, setDraft,
}: {
  parsed: JsdParsed;
  editing: boolean;
  draft: JsdDraft | null;
  setDraft: (d: JsdDraft) => void;
}) {
  // In view mode we render the parsed values directly. In edit mode the
  // draft drives the inputs.
  const fflags = editing && draft ? draft.fflags : parsed.flags_int;
  const ubArmour = editing && draft ? draft.ubArmour : parsed.ubArmour;
  const ubHP = editing && draft ? draft.ubHP : parsed.ubHP;
  const ubDensity = editing && draft ? draft.ubDensity : parsed.ubDensity;
  const offX = editing && draft ? draft.bZTileOffsetX : parsed.bZTileOffsetX;
  const offY = editing && draft ? draft.bZTileOffsetY : parsed.bZTileOffsetY;

  return (
    <div className="space-y-2">
      {/* Flag chip checklist */}
      <div>
        <div className="text-[10px] uppercase tracking-wider text-gray-500">
          Flags <span className="font-mono normal-case text-gray-600">
            0x{fflags.toString(16).padStart(4, "0").toUpperCase()}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap gap-1">
          {JSD_FLAG_BITS.map(({ bit, name }) => {
            const set = (fflags & bit) !== 0;
            const tooltip = `bit 0x${bit.toString(16).toUpperCase()} (${bit})`;
            return (
              <button
                key={bit}
                type="button"
                disabled={!editing}
                onClick={() => {
                  if (!editing || !draft) return;
                  setDraft({ ...draft, fflags: set ? fflags & ~bit : fflags | bit });
                }}
                title={tooltip}
                className={`rounded border px-1.5 py-0.5 text-[10px] ${
                  set
                    ? "border-emerald-600 bg-emerald-900/50 text-emerald-200"
                    : "border-gray-700 bg-gray-900 text-gray-500"
                } ${editing ? "cursor-pointer hover:border-gray-500" : "cursor-default opacity-80"}`}
              >
                {name}
              </button>
            );
          })}
        </div>
      </div>

      {/* Stat grid: armour / HP / density */}
      <div className="grid grid-cols-3 gap-2">
        <U8Field
          label="ubArmour"
          value={ubArmour}
          editing={editing}
          onChange={(v) => draft && setDraft({ ...draft, ubArmour: v })}
        />
        <U8Field
          label="ubHP"
          value={ubHP}
          editing={editing}
          onChange={(v) => draft && setDraft({ ...draft, ubHP: v })}
        />
        <U8Field
          label="ubDensity"
          value={ubDensity}
          editing={editing}
          onChange={(v) => draft && setDraft({ ...draft, ubDensity: v })}
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <I8Field
          label="bZTileOffsetX"
          value={offX}
          editing={editing}
          onChange={(v) => draft && setDraft({ ...draft, bZTileOffsetX: v })}
        />
        <I8Field
          label="bZTileOffsetY"
          value={offY}
          editing={editing}
          onChange={(v) => draft && setDraft({ ...draft, bZTileOffsetY: v })}
        />
      </div>
      {/* Read-only structural fields. Editing these is out of scope. */}
      <div className="grid grid-cols-2 gap-2 text-[10px] text-gray-500">
        <div>
          ubNumberOfTiles:{" "}
          <span className="font-mono text-gray-300">{parsed.ubNumberOfTiles}</span>{" "}
          <span className="text-gray-600">(read-only)</span>
        </div>
        <div>
          struct_data_size:{" "}
          <span className="font-mono text-gray-300">{parsed.struct_data_size}</span>{" "}
          <span className="text-gray-600">(derived)</span>
        </div>
      </div>
    </div>
  );
}

function U8Field({
  label, value, editing, onChange,
}: {
  label: string;
  value: number;
  editing: boolean;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="block text-[10px] uppercase tracking-wider text-gray-500">
        {label}
      </span>
      <input
        type="number"
        min={0} max={255}
        value={value}
        disabled={!editing}
        onChange={(e) => {
          const v = parseInt(e.target.value, 10);
          if (Number.isFinite(v)) onChange(Math.max(0, Math.min(255, v)));
        }}
        className="w-full rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 font-mono text-[11px] disabled:opacity-70"
      />
    </label>
  );
}

function I8Field({
  label, value, editing, onChange,
}: {
  label: string;
  value: number;
  editing: boolean;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="block text-[10px] uppercase tracking-wider text-gray-500">
        {label}
      </span>
      <input
        type="number"
        min={-128} max={127}
        value={value}
        disabled={!editing}
        onChange={(e) => {
          const v = parseInt(e.target.value, 10);
          if (Number.isFinite(v)) onChange(Math.max(-128, Math.min(127, v)));
        }}
        className="w-full rounded border border-gray-700 bg-gray-900 px-1.5 py-0.5 font-mono text-[11px] disabled:opacity-70"
      />
    </label>
  );
}

// ─── Per-tile row ────────────────────────────────────────────────────

function TileRow({
  tile, index, editing, draft, setDraft,
}: {
  tile: JsdProfileTile;
  index: number;
  editing: boolean;
  draft: JsdDraft | null;
  setDraft: (d: JsdDraft) => void;
}) {
  // For draft mode we read from draft.tiles[index]; otherwise from the
  // parsed tile directly. Fall back to the parsed tile if the draft's
  // tiles array is somehow shorter — the draft is always initialized
  // from the parsed JSD, so this is defensive only.
  const cur = (editing && draft ? draft.tiles[index] : tile) ?? tile;

  function patchTile(patch: Partial<JsdProfileTile>) {
    if (!editing || !draft) return;
    const next = [...draft.tiles];
    next[index] = { ...next[index], ...patch } as JsdProfileTile;
    setDraft({ ...draft, tiles: next });
  }

  return (
    <div className="rounded border border-gray-800 bg-gray-950/60 p-1.5">
      <div className="flex items-baseline gap-2 text-[10px]">
        <span className="font-mono text-gray-400">tile {index}</span>
        <Field
          label="bX"
          value={cur.bXPos}
          editing={editing}
          min={-128} max={127}
          onChange={(v) => patchTile({ bXPos: v })}
        />
        <Field
          label="bY"
          value={cur.bYPos}
          editing={editing}
          min={-128} max={127}
          onChange={(v) => patchTile({ bYPos: v })}
        />
        <Field
          label="sPos"
          value={cur.sPosRelToBase}
          editing={editing}
          min={-32768} max={32767}
          onChange={(v) => patchTile({ sPosRelToBase: v })}
        />
      </div>
      <div className="mt-1">
        <ProfileGrid
          grid={cur.profile}
          editing={editing}
          onChange={(grid) => patchTile({ profile: grid })}
        />
      </div>
    </div>
  );
}

function Field({
  label, value, editing, min, max, onChange,
}: {
  label: string;
  value: number;
  editing: boolean;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex items-center gap-1">
      <span className="text-gray-500">{label}</span>
      <input
        type="number"
        min={min} max={max}
        value={value}
        disabled={!editing}
        onChange={(e) => {
          const v = parseInt(e.target.value, 10);
          if (Number.isFinite(v)) onChange(Math.max(min, Math.min(max, v)));
        }}
        className="w-14 rounded border border-gray-700 bg-gray-900 px-1 py-0 font-mono text-[10px] disabled:opacity-70"
      />
    </label>
  );
}

function ProfileGrid({
  grid, editing, onChange,
}: {
  grid: number[][];
  editing: boolean;
  onChange: (g: number[][]) => void;
}) {
  // 5x5 grid of byte values. View mode: colored cells (intensity from
  // value). Edit mode: same but click to enter value via prompt() —
  // keeps the markup small. A nicer popover could replace prompt later.
  return (
    <div className="inline-grid grid-cols-5 gap-px rounded border border-gray-700 bg-gray-900 p-px">
      {grid.flatMap((row, r) =>
        row.map((v, c) => {
          const intensity = Math.min(255, v) / 255;
          const bg = `rgba(110, 231, 183, ${0.05 + intensity * 0.6})`;
          return (
            <button
              key={`${r}-${c}`}
              type="button"
              disabled={!editing}
              onClick={() => {
                if (!editing) return;
                const raw = window.prompt(
                  `profile[${r}][${c}] (0-255)`,
                  String(v),
                );
                if (raw === null) return;
                const nv = parseInt(raw, 10);
                if (!Number.isFinite(nv) || nv < 0 || nv > 255) return;
                const next = grid.map((row2) => [...row2]);
                const targetRow = next[r];
                if (!targetRow) return;
                targetRow[c] = nv;
                onChange(next);
              }}
              title={`profile[${r}][${c}] = ${v}`}
              style={{ backgroundColor: bg }}
              className={`flex h-5 w-5 items-center justify-center font-mono text-[8px] text-gray-200 ${
                editing ? "cursor-pointer hover:ring-1 hover:ring-emerald-400" : "cursor-default"
              }`}
            >
              {v}
            </button>
          );
        })
      )}
    </div>
  );
}

// ─── Draft model + body conversion ───────────────────────────────────

interface JsdDraft {
  fflags: number;
  ubArmour: number;
  ubHP: number;
  ubDensity: number;
  bZTileOffsetX: number;
  bZTileOffsetY: number;
  tiles: JsdProfileTile[];
}

function draftFromParsed(p: JsdParsed): JsdDraft {
  return {
    fflags: p.flags_int,
    ubArmour: p.ubArmour,
    ubHP: p.ubHP,
    ubDensity: p.ubDensity,
    bZTileOffsetX: p.bZTileOffsetX,
    bZTileOffsetY: p.bZTileOffsetY,
    tiles: p.tiles.map((t) => ({
      bXPos: t.bXPos,
      bYPos: t.bYPos,
      sPosRelToBase: t.sPosRelToBase,
      profile: t.profile.map((r) => [...r]),
    })),
  };
}

/** Compute a minimal `JsdEditBody` — only fields that differ from the
 * original get sent. Saves on the backend doing redundant byte-rewrites
 * and keeps the wire payload small. */
function bodyFromDraft({
  xml, tileset, slot, draft, original,
}: {
  xml: string;
  tileset: number;
  slot: number;
  draft: JsdDraft;
  original: JsdParsed;
}): JsdEditBody {
  const body: JsdEditBody = { xml, tileset, slot };
  if (draft.fflags !== original.flags_int) body.fflags = draft.fflags;
  if (draft.ubArmour !== original.ubArmour) body.ubArmour = draft.ubArmour;
  if (draft.ubHP !== original.ubHP) body.ubHP = draft.ubHP;
  if (draft.ubDensity !== original.ubDensity) body.ubDensity = draft.ubDensity;
  if (draft.bZTileOffsetX !== original.bZTileOffsetX)
    body.bZTileOffsetX = draft.bZTileOffsetX;
  if (draft.bZTileOffsetY !== original.bZTileOffsetY)
    body.bZTileOffsetY = draft.bZTileOffsetY;

  const tilePatches = draft.tiles
    .map((t, i) => {
      const o = original.tiles[i];
      if (!o) return null;
      const patch: {
        index: number;
        bXPos?: number;
        bYPos?: number;
        sPosRelToBase?: number;
        profile?: number[][];
      } = { index: i };
      let changed = false;
      if (t.bXPos !== o.bXPos) { patch.bXPos = t.bXPos; changed = true; }
      if (t.bYPos !== o.bYPos) { patch.bYPos = t.bYPos; changed = true; }
      if (t.sPosRelToBase !== o.sPosRelToBase) {
        patch.sPosRelToBase = t.sPosRelToBase;
        changed = true;
      }
      if (!gridsEqual(t.profile, o.profile)) {
        patch.profile = t.profile;
        changed = true;
      }
      return changed ? patch : null;
    })
    .filter((x): x is NonNullable<typeof x> => x !== null);
  if (tilePatches.length > 0) body.tiles = tilePatches;
  return body;
}

function gridsEqual(a: number[][], b: number[][]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const ar = a[i]!;
    const br = b[i];
    if (!br || ar.length !== br.length) return false;
    for (let j = 0; j < ar.length; j++) {
      if (ar[j] !== br[j]) return false;
    }
  }
  return true;
}
