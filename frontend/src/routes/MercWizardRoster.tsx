/**
 * Roster-centric main work surface for the Merc Wizard half of Merc
 * Forge. Replaces the old verb-first Hub tiles (Create / Edit / Copy /
 * Move / Delete / Export / Import) with a subject-first flow: see all
 * slots, pick the merc (or empty slot) you want to act on, then the
 * action surfaces in a context menu + bottom action bar.
 *
 * Behaviors:
 *   - Click a slot → select.
 *   - Right-click → context menu at cursor with the same actions.
 *   - Bottom action bar reflects the selection and is the keyboard /
 *     touch-friendly path.
 *   - Selecting a FILLED slot then clicking "Create new merc here"
 *     pops ONE replace-confirmation; on confirm it silently deletes
 *     the existing merc + opens the Create wizard pre-targeted to
 *     that slot. The user never has to click through a separate
 *     Delete page.
 *   - Selection sidebar on the right shows the active merc's name +
 *     basic facts so the user knows what they're about to act on.
 *
 * Everything that USED to live behind a Hub tile is reachable from
 * here. The old verb-first routes (`/create`, `/edit`, etc.) are still
 * live as URLs (so existing keyboard shortcuts / bookmarks work) but
 * they're no longer linked from the Hub.
 */
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteMerc,
  formatApiError,
  getApiBaseUrl,
  getRoster,
  getRosterPortraitSheet,
} from "../lib/api";
import { getServerToken } from "../lib/tauri";
import type { RosterEntry } from "../lib/schema";
import { tierStyle } from "../lib/slotLocks";
import { categoryLabel, useSlotPicker } from "../lib/slotPicker";

// Reusing the same 256-slot grid logic from SlotPicker but scoped to
// this route. Local copy here avoids importing the SlotPicker's
// AIM/MERC range data — the roster view doesn't care which slots are
// "highlighted by category"; every slot is interactive.

type Filter = "all" | "filled" | "aim" | "merc" | "rpc" | "npc" | "quest_bound" | "locked" | "unassigned" | "empty";

// Card-size presets for the AIM-style roster grid. `min` is the grid's
// min column width (px); cards flex up from there to fill the row.
const CARD_SIZES = [
  { id: "compact", label: "Compact", min: 88 },
  { id: "medium", label: "Medium", min: 120 },
  { id: "large", label: "Large", min: 164 },
] as const;
const CARD_SIZE_KEY = "mw_roster_card_size";
// BigFace native dimensions — the portrait sheet's cell size and the
// card's portrait aspect ratio. Used as a fallback before the sheet
// manifest loads so empty/loading cards keep the same shape.
const BIGFACE_W = 106;
const BIGFACE_H = 122;

export default function MercWizardRoster() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const roster = useQuery({ queryKey: ["roster"], queryFn: () => getRoster() });
  const picker = useSlotPicker();
  // Resolve the sidecar's base URL once. The 256 grid cells then build
  // `<img src>` strings synchronously from it, instead of each firing
  // its own async port-discovery dance. Cached `staleTime: Infinity`
  // because the port doesn't change mid-session.
  const apiBase = useQuery({
    queryKey: ["api-base-url"],
    queryFn: () => getApiBaseUrl(),
    staleTime: Infinity,
  });
  // Per-session token resolved once. `<img src=>` can't attach the
  // X-MercWizard-Token header (browser limitation on element-driven
  // loads), so the token rides as a `?_t=<token>` query param.
  // Pre-fix the portrait URLs 401'd silently and every cell fell back
  // to its slot-number placeholder.
  const apiToken = useQuery({
    queryKey: ["api-server-token"],
    queryFn: () => getServerToken(),
    staleTime: Infinity,
  });
  // Build the `&_t=...` suffix once per render. Empty when the token
  // hasn't resolved yet OR when running browser-dev (no token needed).
  const authQs = apiToken.data
    ? `&_t=${encodeURIComponent(apiToken.data)}`
    : "";

  // Roster portrait sprite-sheet: ONE PNG + ONE JSON manifest replaces
  // the N+1 per-slot fetches. `size: bigface` gives the AIM-dossier
  // head-and-shoulders framing the cards render. Re-bakes on any roster
  // mutation via the dataUpdatedAt cache-bust. The key/size/cacheBust
  // here MUST match App.tsx's warm-prefetch so the startup warm lands on
  // the same cache entry this query reads. Blob URLs are revoked centrally
  // in main.tsx on cache eviction (NOT on unmount — that blanked
  // portraits on a quick return visit, since staleTime is Infinity).
  const portraitSheet = useQuery({
    queryKey: ["roster-portrait-sheet", roster.dataUpdatedAt],
    queryFn: () => getRosterPortraitSheet({
      size: "bigface",
      cacheBust: roster.dataUpdatedAt,
    }),
    staleTime: Infinity,
    enabled: !!roster.data,
  });
  // Quick slot → cell lookup so each SlotCell finds its crop in O(1).
  const cellsBySlot = useMemo(() => {
    const m = new Map<number, { x: number; y: number }>();
    const cells = portraitSheet.data?.manifest.cells ?? [];
    for (const c of cells) m.set(c.slot, { x: c.x, y: c.y });
    return m;
  }, [portraitSheet.data]);
  const [selected, setSelected] = useState<number | null>(null);
  // Default to the filled-mercs gallery (a true AIM-style roster) rather
  // than all 256 slots — far less scrolling now that cards are big. The
  // ALL / EMPTY chips switch to the full grid / empty slots in one click.
  const [filter, setFilter] = useState<Filter>("filled");
  const [search, setSearch] = useState("");
  // Card-size control (Compact / Medium / Large), persisted so the user's
  // choice sticks between sessions. Drives both the responsive grid's min
  // column width and the portrait scale.
  const [sizeIdx, setSizeIdx] = useState<number>(() => {
    try {
      const raw = localStorage.getItem(CARD_SIZE_KEY);
      const n = raw != null ? parseInt(raw, 10) : 1;
      return Number.isInteger(n) && n >= 0 && n < CARD_SIZES.length ? n : 1;
    } catch {
      return 1;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(CARD_SIZE_KEY, String(sizeIdx)); } catch { /* ignore */ }
  }, [sizeIdx]);
  const cardSize = CARD_SIZES[sizeIdx] ?? CARD_SIZES[1]!;
  const cardMin = cardSize.min;
  const [contextMenu, setContextMenu] = useState<{
    slot: number; x: number; y: number;
  } | null>(null);
  const [replaceConfirm, setReplaceConfirm] = useState<{
    slot: number; existingName: string;
  } | null>(null);

  // Close the context menu on any global click / Escape so it doesn't
  // get stuck open when the user clicks somewhere unrelated.
  useEffect(() => {
    if (!contextMenu && !replaceConfirm) return;
    const onClick = () => setContextMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setContextMenu(null);
        setReplaceConfirm(null);
      }
    };
    window.addEventListener("click", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [contextMenu, replaceConfirm]);

  // Delete + navigate to Create. Used by the "replace with new" flow
  // so the user sees ONE confirmation instead of having to Delete and
  // then separately Create.
  //
  // Failure handling: navigation only fires inside onSuccess, so a
  // failed delete leaves the user on the roster with the modal still
  // open and the error visible. The user can retry (the button is
  // re-enabled once isPending drops) or cancel. Without this gating the
  // user would land on /create even though the previous merc is still
  // sitting in the slot — a real data inconsistency, since the next
  // Create write would either silently overwrite or trip the audit
  // SLOT_OCCUPIED check at submit time.
  const replaceMutation = useMutation({
    mutationFn: (slot: number) => deleteMerc(slot),
    onSuccess: (_data, slot) => {
      qc.invalidateQueries({ queryKey: ["roster"] });
      // Slot picker — the just-cleared slot should flip to empty for
      // the Create flow that's about to open. Bug-review finding E4.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
      setReplaceConfirm(null);
      navigate(`/create?slot=${slot}`);
    },
  });

  // Wipe stale error/loading state whenever a fresh modal opens. Without
  // this, opening Replace on slot B after a failed delete on slot A
  // would render slot A's error message under slot B's confirmation
  // prompt — confusing and misleading.
  useEffect(() => {
    if (replaceConfirm) {
      replaceMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replaceConfirm?.slot]);

  const byIdx = useMemo(() => {
    const m = new Map<number, RosterEntry>();
    for (const e of roster.data ?? []) m.set(e.slot, e);
    return m;
  }, [roster.data]);

  const selectedEntry = selected !== null ? byIdx.get(selected) : undefined;
  const selectedFilled = !!selectedEntry && !selectedEntry.is_empty;
  const selectedInfo = selected !== null ? picker.data?.slots[selected] : undefined;
  const selectedLock = selectedInfo && selectedInfo.tier !== "safe"
    ? { tier: selectedInfo.tier, name: selectedInfo.engine_name, role: selectedInfo.engine_role }
    : null;
  const selectedClass = selectedInfo?.category ?? null;

  // Filter slots based on the current Filter chip + search box. The
  // grid still renders ALL 256 cells so positions stay stable; filter
  // only changes which slots are highlighted vs greyed.
  //
  // Precomputed into a Set<number> so each cell does O(1) lookup, and
  // the whole filter logic only re-runs when filter / search / data
  // changes — not on every render of the parent. Previously the predicate
  // ran 256× per keystroke, which made typing in the search box laggy.
  const visibleSet = useMemo<Set<number>>(() => {
    const result = new Set<number>();
    const slotsByIdx = picker.data?.slots;
    const q = search ? search.toLowerCase() : "";
    for (let slot = 0; slot < 256; slot++) {
      const e = byIdx.get(slot);
      const info = slotsByIdx?.[slot];
      if (q) {
        const name = (e?.name ?? "").toLowerCase();
        const nick = (e?.nickname ?? "").toLowerCase();
        const engine = (info?.engine_name ?? "").toLowerCase();
        if (
          !name.includes(q) && !nick.includes(q)
          && !engine.includes(q) && !String(slot).includes(q)
        ) continue;
      }
      let match: boolean;
      switch (filter) {
        case "filled":      match = !!e && !e.is_empty; break;
        case "aim":         match = !!info && info.aim_row.present; break;
        case "merc":        match = !!info && info.merc_row.present; break;
        case "rpc":         match = info?.category === "rpc"; break;
        case "npc":         match = info?.category === "npc"; break;
        case "quest_bound": match = info?.tier === "quest_bound"; break;
        case "locked":      match = info?.tier === "locked"; break;
        case "unassigned":  match = info?.category === "unassigned"; break;
        case "empty":       match = !e || e.is_empty; break;
        case "all":
        default:            match = true; break;
      }
      if (match) result.add(slot);
    }
    return result;
  }, [filter, search, byIdx, picker.data]);

  // The gallery renders ONLY the matching slots (sorted), not all 256
  // greyed — so the filled-first default is a tight gallery and ALL shows
  // the full grid. Slot numbers on each card keep positions legible.
  const visibleSlots = useMemo(
    () => Array.from(visibleSet).sort((a, b) => a - b),
    [visibleSet],
  );

  // Chip counts — computed ignoring the search box (counts reflect the
  // filter shape, not the search-narrowed result). One walk over 256
  // slots tallies all 10 chip totals; recomputes only when the roster
  // or slot-picker data changes.
  const filterCounts = useMemo<Record<Filter, number>>(() => {
    const counts: Record<Filter, number> = {
      all: 256, filled: 0, empty: 0, aim: 0, merc: 0, rpc: 0, npc: 0,
      quest_bound: 0, locked: 0, unassigned: 0,
    };
    const slotsByIdx = picker.data?.slots;
    for (let slot = 0; slot < 256; slot++) {
      const e = byIdx.get(slot);
      const info = slotsByIdx?.[slot];
      const filled = !!e && !e.is_empty;
      if (filled) counts.filled++;
      else counts.empty++;
      if (info?.aim_row.present) counts.aim++;
      if (info?.merc_row.present) counts.merc++;
      if (info?.category === "rpc") counts.rpc++;
      if (info?.category === "npc") counts.npc++;
      if (info?.tier === "quest_bound") counts.quest_bound++;
      if (info?.tier === "locked") counts.locked++;
      if (info?.category === "unassigned") counts.unassigned++;
    }
    return counts;
  }, [byIdx, picker.data]);

  // Stable callbacks so SlotCell's React.memo doesn't trip on a fresh
  // closure each render. The slot is passed back via the callback param
  // so the parent's setSelected / openContext logic stays the source of
  // truth without recreating one closure per cell per render.
  const handleSlotSelect = useCallback((slot: number) => {
    setSelected(slot);
  }, []);
  const handleSlotContextMenu = useCallback(
    (slot: number, x: number, y: number) => {
      setSelected(slot);
      const menuW = 220;
      const menuH = 320;
      const cx = Math.min(x, window.innerWidth - menuW - 8);
      const cy = Math.min(y, window.innerHeight - menuH - 8);
      setContextMenu({ slot, x: cx, y: cy });
    },
    [],
  );

  function handleAction(action: ContextAction, slot: number) {
    setContextMenu(null);
    const entry = byIdx.get(slot);
    const filled = !!entry && !entry.is_empty;
    switch (action) {
      case "create":
        if (filled) {
          // Replace flow: confirm once, silently delete + open Create
          // at this slot. Per spec — the user doesn't have to
          // walk through a separate Delete page.
          setReplaceConfirm({
            slot,
            existingName: entry?.nickname ?? entry?.name ?? `slot ${slot}`,
          });
        } else {
          navigate(`/create?slot=${slot}`);
        }
        break;
      case "edit": navigate(`/edit?slot=${slot}`); break;
      case "duplicate-from": navigate(`/duplicate?from=${slot}`); break;
      case "duplicate-to": navigate(`/duplicate?to=${slot}`); break;
      case "move-from": navigate(`/move?from=${slot}`); break;
      case "delete": navigate(`/delete?slot=${slot}`); break;
      case "export": navigate(`/export?slot=${slot}`); break;
      case "import": navigate(`/import?slot=${slot}`); break;
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Merc Wizard</h1>
          <p className="text-sm text-wasteland-400 mt-0.5">
            Click a slot to select. Right-click for a context menu.
            Actions also appear in the bottom bar.
          </p>
        </div>
        <Link
          to="/hub"
          className="text-sm text-wasteland-400 hover:text-rust-400 underline underline-offset-2"
        >
          ← Back to Hub
        </Link>
      </div>

      {/* Filter / search bar */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap overflow-hidden rounded border border-wasteland-700">
          {/* Filter chips: FILLED (the default gallery) / ALL (full 256-
              slot grid) / AIM / MERC / EMPTY. The Filter union type still
              defines the dropped values (rpc, npc, quest_bound, locked,
              unassigned) so deep-linked URLs that set ?filter=locked keep
              working; we just don't surface the buttons. */}
          {(["filled", "all", "aim", "merc", "empty"] as Filter[]).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`px-3 py-1 text-xs uppercase ${
                filter === f
                  ? "bg-rust-500 text-wasteland-50"
                  : "bg-wasteland-900 text-wasteland-300 hover:bg-wasteland-800"
              }`}
            >
              {f.replace("_", " ")}
              <span
                className={`ml-1.5 font-mono normal-case text-[10px] ${
                  filter === f ? "text-wasteland-100/80" : "text-wasteland-500"
                }`}
              >
                {filterCounts[f]}
              </span>
            </button>
          ))}
        </div>
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by name, nickname, or slot number…"
          className="flex-1 min-w-[16rem] rounded border border-wasteland-700 bg-wasteland-900 px-3 py-1 text-xs"
        />
        {/* Card-size control */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase text-wasteland-500">Size</span>
          <input
            type="range"
            min={0}
            max={CARD_SIZES.length - 1}
            step={1}
            value={sizeIdx}
            onChange={(e) => setSizeIdx(parseInt(e.target.value, 10))}
            className="w-24 accent-rust-500"
            aria-label="Portrait card size"
          />
          <span className="w-14 text-[10px] text-wasteland-400">
            {cardSize.label}
          </span>
        </div>
      </div>

      {roster.isLoading && (
        <div className="text-wasteland-400 text-sm">Loading roster…</div>
      )}
      {roster.error && (
        <div className="rounded border border-red-500/60 bg-red-500/10 p-3 text-sm text-red-300">
          {String(roster.error)}
        </div>
      )}

      {roster.data && (
        <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
          {/* Slot grid */}
          <div>
            {visibleSlots.length === 0 ? (
              <div className="rounded border border-wasteland-700 bg-wasteland-900/40 p-6 text-center text-sm text-wasteland-400">
                No slots match. Try the ALL chip or clear the search box.
              </div>
            ) : (
              <div
                className="grid gap-2"
                style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${cardMin}px, 1fr))` }}
              >
                {visibleSlots.map((slot) => {
                  const entry = byIdx.get(slot);
                  const filled = !!entry && !entry.is_empty;
                  const isSelected = selected === slot;
                  const info = picker.data?.slots[slot];
                  const tier = info?.tier ?? "safe";
                  const cell = filled ? cellsBySlot.get(slot) ?? null : null;
                  const sheet = portraitSheet.data;
                  const nick = filled && entry
                    ? (entry.nickname ?? entry.name ?? "?")
                    : null;
                  return (
                    <SlotCell
                      key={slot}
                      slot={slot}
                      filled={filled}
                      selected={isSelected}
                      tier={tier}
                      engineName={info?.engine_name ?? null}
                      engineRole={info?.engine_role ?? null}
                      profileType={entry?.profile_type ?? null}
                      sheetUrl={cell && sheet ? sheet.blobUrl : null}
                      cellX={cell?.x ?? 0}
                      cellY={cell?.y ?? 0}
                      cellW={sheet?.manifest.cell_w ?? BIGFACE_W}
                      cellH={sheet?.manifest.cell_h ?? BIGFACE_H}
                      sheetW={sheet?.manifest.sheet_w ?? 0}
                      sheetH={sheet?.manifest.sheet_h ?? 0}
                      nickname={nick}
                      onSelect={handleSlotSelect}
                      onContextMenu={handleSlotContextMenu}
                    />
                  );
                })}
              </div>
            )}
            {/* Legend — merc Type (border colour) + slot-lock status */}
            <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-wasteland-400">
              <span className="text-wasteland-500">Type:</span>
              {([1, 2, 3, 4, 5, 6] as const).map((t) => {
                const s = TYPE_STYLE[t]!;
                return (
                  <span key={t} className="flex items-center gap-1.5">
                    <span className={`inline-block w-3 h-3 rounded border-2 ${s.chip}`} />
                    {s.label}
                  </span>
                );
              })}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-wasteland-400">
              <span className="text-wasteland-500">Status:</span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-3 h-3 rounded border-2 border-red-500 bg-red-500/30" />
                Locked
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-3 h-3 rounded border-2 border-violet-500 bg-violet-500/30" />
                Quest-bound
              </span>
            </div>
          </div>

          {/* Selection sidebar */}
          <aside className="rounded border border-wasteland-700 bg-wasteland-900/40 p-3 text-sm">
            <h3 className="text-xs font-semibold uppercase text-wasteland-400 mb-2">
              Selection
            </h3>
            {selected === null ? (
              <p className="text-wasteland-500 text-xs">
                Click a slot in the grid to select it.
              </p>
            ) : (
              <div className="space-y-2">
                {/* BigFace preview — only when a filled slot is selected
                    AND apiBase resolved. The portrait endpoint serves a
                    decoded BigFace.STI frame[0] at 106x122 native; we
                    render it at native size for sharpness. Falls back to
                    null on 204 (no portrait) or 404 (STI missing). */}
                {selectedFilled && apiBase.data && apiToken.data !== undefined && (
                  <BigFacePreview
                    src={`${apiBase.data}/merc/${selected}/portrait?size=bigface&v=${roster.dataUpdatedAt}${authQs}`}
                  />
                )}
                <div>
                  <div className="text-[10px] uppercase text-wasteland-500">Slot</div>
                  <div className="font-mono text-lg text-wasteland-100">{selected}</div>
                </div>
                {selectedFilled ? (
                  <>
                    <div>
                      <div className="text-[10px] uppercase text-wasteland-500">Name</div>
                      <div className="text-wasteland-100">
                        {selectedEntry?.nickname ?? selectedEntry?.name ?? "?"}
                      </div>
                      {selectedEntry?.name && selectedEntry?.nickname
                        && selectedEntry.name !== selectedEntry.nickname && (
                        <div className="text-[10px] text-wasteland-500">
                          {selectedEntry.name}
                        </div>
                      )}
                    </div>
                    <div>
                      <div className="text-[10px] uppercase text-wasteland-500">Type</div>
                      <div className="text-wasteland-200 text-xs">
                        {profileTypeLabel(selectedEntry?.profile_type ?? 0)}
                      </div>
                    </div>
                    {selectedClass && (
                      <div>
                        <div className="text-[10px] uppercase text-wasteland-500">Category</div>
                        <div className="text-wasteland-200 text-xs">
                          {categoryLabel(selectedClass)}
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="text-wasteland-400 italic text-xs">
                    Empty slot. Create a new merc here, import a .wmerc,
                    or copy an existing one in.
                  </div>
                )}
                {selectedLock && (
                  <div className={`rounded border p-2 text-[10px] ${tierStyle(selectedLock.tier).tileClass}`}>
                    <div className="font-semibold">{tierStyle(selectedLock.tier).label}</div>
                    {selectedLock.role && (
                      <div className="mt-0.5 text-wasteland-200">{selectedLock.role}</div>
                    )}
                  </div>
                )}
              </div>
            )}
          </aside>
        </div>
      )}

      {/* Action bar — bottom-anchored, always visible */}
      <div className="mt-4 sticky bottom-2 z-20">
        <div className="rounded-lg border border-wasteland-700 bg-wasteland-950/95 backdrop-blur p-3 shadow-lg">
          {selected === null ? (
            <p className="text-xs text-wasteland-500 text-center">
              Select a slot to see available actions.
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-wasteland-400 mr-2">
                Slot {selected}:
              </span>
              <ActionButtons
                selectedFilled={selectedFilled}
                onPick={(a) => handleAction(a, selected)}
              />
            </div>
          )}
        </div>
      </div>

      {/* Right-click context menu — same actions, positioned at cursor */}
      {contextMenu && (
        <div
          className="fixed z-50 w-56 rounded-lg border border-wasteland-700 bg-wasteland-950 shadow-2xl"
          style={{ top: contextMenu.y, left: contextMenu.x }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="px-3 py-2 border-b border-wasteland-800 text-xs text-wasteland-400">
            Slot {contextMenu.slot}
            {byIdx.get(contextMenu.slot)?.is_empty === false && (
              <span className="ml-2 text-wasteland-200 font-mono">
                · {byIdx.get(contextMenu.slot)?.nickname
                  ?? byIdx.get(contextMenu.slot)?.name ?? "?"}
              </span>
            )}
          </div>
          <ContextMenuItems
            selectedFilled={!!byIdx.get(contextMenu.slot)
              && !byIdx.get(contextMenu.slot)?.is_empty}
            onPick={(a) => handleAction(a, contextMenu.slot)}
          />
        </div>
      )}

      {/* Replace-with-new confirmation. Single dialog, one click to
          proceed. Behind the scenes: delete then navigate to /create. */}
      {replaceConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setReplaceConfirm(null)}
        >
          <div
            className="card max-w-md w-full space-y-4"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="flex items-start gap-3">
              <div className="text-2xl text-yellow-400">⚠</div>
              <div>
                <h2 className="text-lg font-bold">
                  Replace {replaceConfirm.existingName}?
                </h2>
                <p className="text-sm text-wasteland-300 mt-1">
                  Slot {replaceConfirm.slot} already has{" "}
                  <span className="font-semibold">{replaceConfirm.existingName}</span>.
                  Continuing will delete them and open the Create wizard
                  so you can put a new merc in this slot.
                </p>
                <p className="text-xs text-wasteland-500 mt-2">
                  A backup is taken automatically before the delete, so
                  you can restore from <Link to="/backups" className="underline">Backups</Link>{" "}
                  if you change your mind.
                </p>
              </div>
            </div>
            {/* Error block sits ABOVE the buttons so the user sees what
                went wrong before they reach for Retry. Without this the
                error would render below the Cancel/Delete row and a
                rapid second-click would re-submit before they read it. */}
            {replaceMutation.error && (
              <div
                role="alert"
                className="rounded border border-red-500/60 bg-red-950/60 px-3 py-2 text-xs text-red-200"
              >
                <div className="font-semibold mb-0.5">Delete failed</div>
                <div>{formatApiError(replaceMutation.error)}</div>
                <div className="mt-1 text-[10px] text-red-300/80">
                  The merc is still in slot {replaceConfirm.slot}. Try again
                  or cancel to keep them as-is.
                </div>
              </div>
            )}
            {/* Real progress indicator during the delete. Without this
                the modal sat with only a button-label "Deleting…" while
                the backend snapshotted a backup, removed the profile
                row, sliced the EDT, cleared gear, and cleared voice
                clips — a multi-second sequence on heavily-modded
                installs. Pulsing indeterminate bar + phase text so the
                user sees something is happening. */}
            {replaceMutation.isPending && (
              <div
                role="status"
                aria-busy="true"
                className="rounded border border-blue-700 bg-blue-950/40 px-3 py-2 text-xs"
              >
                <div className="mb-1.5 flex items-center gap-2 text-blue-200">
                  <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-blue-300 border-t-transparent" />
                  Snapshotting backup → removing profile → clearing gear → routing to Create…
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full border border-blue-900 bg-blue-950">
                  <div className="h-full w-full bg-gradient-to-r from-blue-700/30 via-blue-400/60 to-blue-700/30 animate-pulse" />
                </div>
              </div>
            )}
            <div className="flex items-center justify-end gap-2 border-t border-wasteland-700 pt-3">
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setReplaceConfirm(null)}
                disabled={replaceMutation.isPending}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => replaceMutation.mutate(replaceConfirm.slot)}
                disabled={replaceMutation.isPending}
              >
                {replaceMutation.isPending
                  ? "Deleting…"
                  : replaceMutation.error
                    ? "Retry Delete"
                    : "Delete & Create New"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// BigFace preview for the selection sidebar. 106x122 native; rendered
// at native dimensions so palette pixels stay sharp. Hides itself
// silently when the STI is missing — the sidebar's name/type rows below
// still give the user enough info to act, so no need for a visible
// "portrait unavailable" placeholder.
function BigFacePreview({ src }: { src: string }) {
  const [ok, setOk] = useState(true);
  // Re-show when the URL changes (different slot selected, or roster
  // invalidated and cache buster bumped).
  useEffect(() => { setOk(true); }, [src]);
  if (!ok) return null;
  return (
    <div className="flex justify-center">
      <img
        src={src}
        alt=""
        loading="lazy"
        decoding="async"
        draggable={false}
        onError={() => setOk(false)}
        // Native 106x122. `image-rendering: pixelated` keeps the
        // engine's pixel art crisp at any browser zoom level; without it
        // some browsers apply a blur filter that softens the palette.
        className="rounded border border-wasteland-700 bg-wasteland-950"
        style={{ width: 106, height: 122, imageRendering: "pixelated" }}
      />
    </div>
  );
}

// Per-cell renderer. React.memo'd so re-renders only fire when one of
// its (all-primitive) props changes. Parent passes stable callbacks via
// useCallback so the onSelect/onContextMenu identities don't trip the
// memo. The cls string and tip text are built inside the cell from
// primitives — keeping them out of the parent's render saves 256 string
// concatenations per keystroke into the filter input.
// Merc Type (MercProfiles <Type>): 1 AIM, 2 MERC, 3 RPC, 4 NPC, 5 Vehicle,
// 6 IMP (0/None gets no tint). Border-colours each filled cell so the category
// is readable at a glance; a slot-lock tier still overrides this for safety.
const TYPE_STYLE: Record<number, { label: string; border: string; chip: string }> = {
  1: { label: "AIM", border: "border-sky-500", chip: "border-sky-500 bg-sky-500/30" },
  2: { label: "MERC", border: "border-emerald-500", chip: "border-emerald-500 bg-emerald-500/30" },
  3: { label: "RPC", border: "border-orange-500", chip: "border-orange-500 bg-orange-500/30" },
  4: { label: "NPC", border: "border-teal-400", chip: "border-teal-400 bg-teal-400/30" },
  5: { label: "Vehicle", border: "border-lime-500", chip: "border-lime-500 bg-lime-500/30" },
  6: { label: "IMP", border: "border-fuchsia-500", chip: "border-fuchsia-500 bg-fuchsia-500/30" },
};

interface SlotCellProps {
  slot: number;
  filled: boolean;
  selected: boolean;
  /** Slot lock tier from useSlotPicker. "safe" when no engine-named lock. */
  tier: string;
  engineName: string | null;
  engineRole: string | null;
  profileType: number | null;
  sheetUrl: string | null;
  cellX: number;
  cellY: number;
  cellW: number;
  cellH: number;
  /** Full sprite-sheet dimensions — needed to scale the cell crop to fill
   *  a flexible-width card via percentage background sizing. */
  sheetW: number;
  sheetH: number;
  nickname: string | null;
  /** Stable refs from the parent's useCallback. */
  onSelect: (slot: number) => void;
  onContextMenu: (slot: number, x: number, y: number) => void;
}

const SlotCell = memo(function SlotCell({
  slot, filled, selected, tier, engineName, engineRole, profileType,
  sheetUrl, cellX, cellY, cellW, cellH, sheetW, sheetH, nickname,
  onSelect, onContextMenu,
}: SlotCellProps) {
  const showPortrait = !!sheetUrl;
  const lockStyle = tier !== "safe" ? tierStyle(tier as Parameters<typeof tierStyle>[0]) : null;
  const typeStyle = profileType != null ? TYPE_STYLE[profileType] ?? null : null;

  // Percentage-based sprite-sheet crop. The grid's auto-fill 1fr columns
  // stretch each card beyond the min width, so a fixed-px crop wouldn't
  // fill it. backgroundSize scales the whole sheet so ONE cell == the card
  // width; backgroundPosition uses the standard sprite %-formula. The
  // portrait area's aspect-ratio is pinned to the cell so height tracks
  // width. This is what makes the portrait fill the card at any size.
  const bgSize = cellW > 0 && cellH > 0
    ? `${(sheetW / cellW) * 100}% ${(sheetH / cellH) * 100}%`
    : "cover";
  const posX = sheetW - cellW > 0 ? (cellX / (sheetW - cellW)) * 100 : 0;
  const posY = sheetH - cellH > 0 ? (cellY / (sheetH - cellH)) * 100 : 0;

  // Card frame: type-coloured border for filled mercs (AIM/MERC/RPC/NPC),
  // dashed for empty slots, rust ring when selected. Lock / quest-bound
  // tiers show as a corner dot so the Type colour stays readable.
  let cls = "relative flex flex-col overflow-hidden rounded-lg border-2 "
    + "text-left transition-colors cursor-pointer ";
  if (selected) {
    cls += "border-rust-300 ring-2 ring-rust-400/70 bg-wasteland-800";
  } else if (filled) {
    cls += `${typeStyle?.border ?? "border-wasteland-600"} bg-wasteland-900 hover:bg-wasteland-800`;
  } else {
    cls += "border-dashed border-wasteland-700 bg-wasteland-950/60 hover:border-wasteland-600 hover:bg-wasteland-900";
  }

  const baseTip = filled
    ? `${nickname ?? "?"} (slot ${slot})`
    : `Empty slot ${slot}`;
  const tip = lockStyle
    ? `${baseTip}\n${lockStyle.label} — ${engineRole ?? engineName ?? "see warning"}`
    : baseTip;

  return (
    <button
      type="button"
      className={cls}
      onClick={() => onSelect(slot)}
      onContextMenu={(e: ReactMouseEvent) => {
        e.preventDefault();
        onContextMenu(slot, e.clientX, e.clientY);
      }}
      title={tip}
    >
      {/* Portrait area — aspect pinned to the BigFace cell so cards align */}
      <div
        className="relative w-full"
        style={{ aspectRatio: `${cellW || BIGFACE_W} / ${cellH || BIGFACE_H}` }}
      >
        {showPortrait ? (
          <div
            aria-hidden="true"
            className="absolute inset-0 pointer-events-none"
            style={{
              backgroundImage: `url(${sheetUrl})`,
              backgroundSize: bgSize,
              backgroundPosition: `${posX}% ${posY}%`,
              backgroundRepeat: "no-repeat",
              imageRendering: "pixelated",
            }}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-wasteland-950/40">
            {profileType === 6 ? (
              // Type=6 IMP destination templates (PGmale*/PGLady*, including
              // the nickname-less PGLady2 at slot 55) have no portrait until a
              // player creates an IMP character into the slot. Label them so
              // they read as intentional empty IMP slots, not a broken/missing
              // portrait — applies whether or not they carry a placeholder
              // nickname (slot 55 has none, so it would otherwise show "+").
              <span className="flex flex-col items-center gap-0.5 text-wasteland-500">
                <span className="font-mono text-sm font-semibold tracking-wide">IMP</span>
                <span className="text-[8px] uppercase tracking-wide">template</span>
              </span>
            ) : filled ? (
              <span className="font-mono text-lg text-wasteland-600">
                {(nickname ?? "?").charAt(0).toUpperCase()}
              </span>
            ) : (
              <span aria-hidden="true" className="text-2xl leading-none text-wasteland-700">+</span>
            )}
          </div>
        )}
        {/* Slot-number badge */}
        <span className="absolute top-1 left-1 z-10 rounded bg-black/70 px-1 font-mono text-[9px] leading-none text-wasteland-100">
          {slot}
        </span>
        {/* Lock / quest-bound corner dot */}
        {lockStyle && (tier === "locked" || tier === "quest_bound") && (
          <span
            aria-hidden="true"
            className={`pointer-events-none absolute top-1 right-1 z-10 h-2.5 w-2.5 rounded-full border-2 ${lockStyle.borderClass} bg-wasteland-950`}
          />
        )}
      </div>
      {/* Name plate */}
      <div className="w-full bg-black/40 px-1.5 py-1">
        {filled ? (
          <>
            <div className="truncate text-[11px] font-medium leading-tight text-wasteland-50">
              {nickname ?? "?"}
            </div>
            <div className="truncate text-[9px] leading-tight text-wasteland-400">
              {typeStyle?.label ?? "—"}
            </div>
          </>
        ) : (
          <div className="truncate text-[10px] leading-tight text-wasteland-500">
            Empty
          </div>
        )}
      </div>
      {selected && <span className="sr-only">selected</span>}
    </button>
  );
});

type ContextAction =
  | "create"
  | "edit"
  | "duplicate-from"
  | "duplicate-to"
  | "move-from"
  | "delete"
  | "export"
  | "import";

function ActionButtons({
  selectedFilled, onPick,
}: {
  selectedFilled: boolean;
  onPick: (a: ContextAction) => void;
}) {
  if (selectedFilled) {
    return (
      <>
        <ActionBtn icon="✏️" label="Edit" onClick={() => onPick("edit")} />
        <ActionBtn icon="📋" label="Copy to…" onClick={() => onPick("duplicate-from")} />
        <ActionBtn icon="✂️" label="Move to…" onClick={() => onPick("move-from")} />
        <ActionBtn icon="📤" label="Export .wmerc" onClick={() => onPick("export")} />
        <span className="mx-1 h-5 w-px bg-wasteland-700" />
        <ActionBtn icon="➕" label="Replace with new" onClick={() => onPick("create")} variant="warn" />
        <ActionBtn icon="🗑️" label="Delete" onClick={() => onPick("delete")} variant="danger" />
      </>
    );
  }
  return (
    <>
      <ActionBtn icon="➕" label="Create new merc here" onClick={() => onPick("create")} />
      <ActionBtn icon="📥" label="Import .wmerc to this slot" onClick={() => onPick("import")} />
      <ActionBtn icon="📋" label="Copy existing merc here" onClick={() => onPick("duplicate-to")} />
    </>
  );
}

function ActionBtn({
  icon, label, onClick, variant = "default",
}: {
  icon: string;
  label: string;
  onClick: () => void;
  variant?: "default" | "warn" | "danger";
}) {
  const cls = variant === "danger"
    ? "border-red-500/60 bg-red-500/15 text-red-200 hover:bg-red-500/25"
    : variant === "warn"
      ? "border-yellow-500/60 bg-yellow-500/15 text-yellow-100 hover:bg-yellow-500/25"
      : "border-wasteland-600 bg-wasteland-800 text-wasteland-100 hover:bg-wasteland-700";
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded border px-3 py-1.5 text-xs ${cls}`}
    >
      <span aria-hidden>{icon}</span>
      <span>{label}</span>
    </button>
  );
}

function ContextMenuItems({
  selectedFilled, onPick,
}: {
  selectedFilled: boolean;
  onPick: (a: ContextAction) => void;
}) {
  if (selectedFilled) {
    return (
      <ul className="py-1">
        <MenuItem icon="✏️" label="Edit" onClick={() => onPick("edit")} />
        <MenuItem icon="📋" label="Copy to…" onClick={() => onPick("duplicate-from")} />
        <MenuItem icon="✂️" label="Move to…" onClick={() => onPick("move-from")} />
        <MenuItem icon="📤" label="Export .wmerc" onClick={() => onPick("export")} />
        <li className="my-1 border-t border-wasteland-800" />
        <MenuItem icon="➕" label="Replace with new" onClick={() => onPick("create")} variant="warn" />
        <MenuItem icon="🗑️" label="Delete" onClick={() => onPick("delete")} variant="danger" />
      </ul>
    );
  }
  return (
    <ul className="py-1">
      <MenuItem icon="➕" label="Create new merc here" onClick={() => onPick("create")} />
      <MenuItem icon="📥" label="Import .wmerc" onClick={() => onPick("import")} />
      <MenuItem icon="📋" label="Copy existing merc here" onClick={() => onPick("duplicate-to")} />
    </ul>
  );
}

function MenuItem({
  icon, label, onClick, variant = "default",
}: {
  icon: string;
  label: string;
  onClick: () => void;
  variant?: "default" | "warn" | "danger";
}) {
  const txt = variant === "danger" ? "text-red-300"
    : variant === "warn" ? "text-yellow-200"
    : "text-wasteland-100";
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-wasteland-800 ${txt}`}
      >
        <span aria-hidden className="w-4 text-center">{icon}</span>
        <span>{label}</span>
      </button>
    </li>
  );
}

// Engine Type values (MercProfiles <Type>): 1 AIM, 2 MERC, 3 RPC, 4 NPC,
// 5 Vehicle, 6 IMP — must match TYPE_STYLE above and models.py ProfileType.
// (3/4 were previously swapped here, contradicting the card chip.)
function profileTypeLabel(t: number): string {
  switch (t) {
    case 1: return "AIM mercenary";
    case 2: return "M.E.R.C. mercenary";
    case 3: return "RPC";
    case 4: return "NPC";
    case 5: return "Vehicle";
    case 6: return "IMP";
    default: return `Type ${t}`;
  }
}
