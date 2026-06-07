/**
 * Engine-faithful slot picker.
 *
 * Replaces the pre-rewrite version (preserved at SlotPicker.legacy.tsx)
 * that hardcoded AIM/MERC slot ranges. The grid now reads its truth from
 * the sidecar's /slots/picker endpoint, which joins MercProfiles +
 * AIMAvailability + MercAvailability + the engine-named-slot table.
 *
 * Each cell shows:
 *   - Slot number
 *   - "A" badge when an AIMAvailability row points here
 *   - "M" badge when a MercAvailability row points here
 *   - "★" badge when the slot is engine-named (enum NPCIDs in Soldier Profile.h)
 *   - Border + fill tint from tier (locked / quest_bound / vanilla_overwrite / safe)
 *
 * Filter pills narrow the highlight to a category. The grid still renders
 * all 255 slots so positions stay stable; non-matching slots dim.
 *
 * The "Engine site view" toggle on the right groups slots by where a merc
 * can be reached in-game: the AIM hiring page (an 8×5 mugshot grid, up to 3
 * pages / 120 mercs) and Speck's M.E.R.C. service (listed separately here —
 * in-game M.E.R.C. shows one merc at a time, not a row list).
 */
import { useState } from "react";

import {
  categoryLabel,
  tooltipFor,
  useSlotPicker,
  type SlotCategory,
  type SlotInfo,
} from "../lib/slotPicker";
import { tierStyle } from "../lib/slotLocks";

interface Props {
  selected: number | null;
  onSelect: (slot: number) => void;
  /** Filter pills default — pass to scope visible highlights. */
  showOnly?: "all" | "empty" | "aim" | "merc" | "quest_bound" | "locked" | "unassigned";
  /** Whether already-filled slots can be selected (default: only empty) */
  allowFilled?: boolean;
  /** Show occupancy for a specific install (defaults to active install) */
  installId?: string;
}

type FilterKey = NonNullable<Props["showOnly"]>;

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all",         label: "All" },
  { key: "empty",       label: "Empty" },
  { key: "aim",         label: "AIM-bound" },
  { key: "merc",        label: "MERC-bound" },
  { key: "quest_bound", label: "Quest-bound" },
  { key: "locked",      label: "Locked" },
  { key: "unassigned",  label: "Unassigned" },
];

function matchesFilter(info: SlotInfo, filter: FilterKey): boolean {
  switch (filter) {
    case "all":         return true;
    case "empty":       return info.is_empty;
    case "aim":         return info.aim_row.present;
    case "merc":        return info.merc_row.present;
    case "quest_bound": return info.tier === "quest_bound";
    case "locked":      return info.tier === "locked";
    case "unassigned":  return info.category === "unassigned";
    default:            return true;
  }
}

export default function SlotPicker({
  selected,
  onSelect,
  showOnly = "all",
  allowFilled = false,
  installId,
}: Props) {
  const picker = useSlotPicker(installId);
  const [filter, setFilter] = useState<FilterKey>(showOnly);
  const [siteView, setSiteView] = useState(false);

  if (picker.isLoading) {
    return <div className="text-wasteland-300">Loading slot picker…</div>;
  }
  if (!picker.data) return null;

  const { slots, engine_flags, aim_row_count, merc_row_count, laptop_aim_display_cap } = picker.data;
  const emptyCount = slots.filter((s) => s.is_empty).length;

  return (
    <>
      {/* Filter pills */}
      <div className="mb-3 flex flex-wrap items-center gap-1">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setFilter(f.key)}
            className={`px-2.5 py-1 text-xs rounded border ${
              filter === f.key
                ? "bg-rust-500 text-wasteland-50 border-rust-400"
                : "bg-wasteland-900 text-wasteland-300 border-wasteland-700 hover:bg-wasteland-800"
            }`}
          >
            {f.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setSiteView((v) => !v)}
          className="ml-auto px-2.5 py-1 text-xs rounded border border-wasteland-700 bg-wasteland-900 text-wasteland-300 hover:bg-wasteland-800"
          title={
            siteView
              ? "Show the full 0-254 slot grid"
              : "Group slots by where a merc can be reached in-game: the AIM hiring page (8×5 mugshot grid, up to 3 pages / 120 mercs — the game's limit) and Speck's M.E.R.C. service"
          }
        >
          {siteView ? "Show full grid" : "Engine site view"}
        </button>
      </div>

      {siteView ? (
        <EngineSiteView
          slots={slots}
          selected={selected}
          onSelect={onSelect}
          allowFilled={allowFilled}
          laptopAimDisplayCap={laptop_aim_display_cap}
        />
      ) : (
        <FullGrid
          slots={slots}
          selected={selected}
          onSelect={onSelect}
          allowFilled={allowFilled}
          filter={filter}
        />
      )}

      {/* Stats line */}
      <div className="mt-2 text-[10px] text-wasteland-500">
        {emptyCount} empty / {slots.length - emptyCount} occupied · {aim_row_count} AIM rows · {merc_row_count} M.E.R.C. rows
        {!allowFilled && emptyCount < 8 && (
          <span className="ml-2 text-yellow-400">
            ⚠ only {emptyCount} empty destination{emptyCount === 1 ? "" : "s"} available — most slots in this install already hold a vanilla / expansion merc.
          </span>
        )}
        {aim_row_count > laptop_aim_display_cap && (
          <span className="ml-2 text-yellow-400">
            ⚠ {aim_row_count - laptop_aim_display_cap} AIM rows exceed the
            laptop's {laptop_aim_display_cap}-mugshot cap
          </span>
        )}
        {engine_flags.is_ub && (
          <span className="ml-2 text-wasteland-400">· UB build (FIRST_RPC=60)</span>
        )}
        {!engine_flags.reads_profiles_from_xml && (
          <span className="ml-2 text-yellow-400">
            · legacy XML profile loading — slots 51-56 are LOCKED
          </span>
        )}
      </div>

      {/* Legend */}
      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-wasteland-400">
        <span>Tier:</span>
        <LegendChip
          color="border-red-500 bg-red-500/30"
          label="Locked"
          title="Reserved by the game engine — vehicles, main-story characters, and a few internal slots. Overwriting won't crash, but the game can silently replace your merc or break a quest."
        />
        <LegendChip
          color="border-violet-500 bg-violet-500/30"
          label="Quest-bound"
          title="Built-in character the game knows by name and weaves into its dialogue and quests. A few can be recruited (like Ira or Dynamo); most are scripted townspeople and quest-givers. Overwriting works, but the game's scripted lines and events for this slot will now point at your new merc."
        />
        <LegendChip
          color="border-yellow-400 bg-yellow-400/30"
          label="Overwrites base character"
          title="A base-game or expansion merc already lives here. Saving puts your merc here in their place, and your merc takes over their spot on the in-game hiring site. Restore a backup if you want the original back for this install."
        />
        <LegendChip
          color="border-wasteland-700"
          label="Safe"
          title="Not tied to any built-in character — free to use for your own mercs. Whether one shows up on the AIM or M.E.R.C. hiring site still depends on its website listing."
        />
        <span className="ml-3">Badges:</span>
        <Badge text="A" tooltip="Has an AIM listing — appears on the AIM hiring page in-game" />
        <Badge text="M" tooltip="Has a M.E.R.C. listing — can show up on Speck's M.E.R.C. service once that merc unlocks (M.E.R.C. reveals its roster gradually as the game progresses)" />
        <Badge text="★" tooltip="A built-in character the game refers to by name (a hireable merc, quest character, or vehicle) — overwriting it replaces that character and can break their quests or dialogue" />
      </div>
    </>
  );
}

function FullGrid({
  slots, selected, onSelect, allowFilled, filter,
}: {
  slots: SlotInfo[];
  selected: number | null;
  onSelect: (slot: number) => void;
  allowFilled: boolean;
  filter: FilterKey;
}) {
  return (
    <div className="grid gap-1 max-w-3xl" style={{ gridTemplateColumns: "repeat(16, minmax(0, 1fr))" }}>
      {slots.map((info) => (
        <SlotCell
          key={info.slot}
          info={info}
          selected={selected === info.slot}
          highlighted={matchesFilter(info, filter)}
          allowFilled={allowFilled}
          onSelect={() => onSelect(info.slot)}
        />
      ))}
    </div>
  );
}

function SlotCell({
  info, selected, highlighted, allowFilled, onSelect,
}: {
  info: SlotInfo;
  selected: boolean;
  highlighted: boolean;
  allowFilled: boolean;
  onSelect: () => void;
}) {
  const filled = !info.is_empty;
  const disabled = !allowFilled && filled;
  const style = tierStyle(info.tier);

  // Show the occupant's nickname (or vanilla engine name) under the slot
  // number whenever something is at the slot, so the user can tell at a
  // glance why this cell is disabled / which character they'd be
  // overwriting. Pre-fix the cell only showed the slot number and filled
  // slots looked nearly identical to empty ones — a user hit this
  // ("198 is the only slot it will let me copy to") because nearly every
  // slot in a heavily-modded install is filled with vanilla / expansion
  // content and the UI didn't surface it.
  const occupantLabel = info.profile_nickname
    ?? info.profile_name
    ?? (info.is_empty ? null : info.engine_name);

  let cls =
    "relative aspect-square rounded text-[8px] font-mono flex flex-col items-center justify-center gap-px transition-colors border overflow-hidden px-px ";
  if (selected) {
    cls += "bg-rust-500 text-wasteland-50 ring-2 ring-rust-300 border-rust-300";
  } else if (filled) {
    cls += allowFilled
      ? `bg-wasteland-700 text-wasteland-100 hover:bg-rust-500/30 ${style.borderClass}`
      : `bg-wasteland-800 text-wasteland-500 cursor-not-allowed opacity-70 ${style.borderClass}`;
  } else if (highlighted) {
    cls += `bg-wasteland-900 text-wasteland-200 hover:bg-rust-500/30 ${style.borderClass}`;
  } else {
    cls += `bg-wasteland-900 text-wasteland-600 hover:bg-wasteland-800 ${style.borderClass} opacity-40`;
  }

  const tip = tooltipFor(info).join("\n");

  return (
    <button
      type="button"
      className={cls}
      disabled={disabled}
      onClick={onSelect}
      title={tip}
    >
      <span className="leading-none text-[10px]">{info.slot}</span>
      {occupantLabel && (
        <span className="leading-none truncate w-full text-center" title={occupantLabel}>
          {occupantLabel}
        </span>
      )}
      <SlotBadges info={info} />
    </button>
  );
}

function SlotBadges({ info }: { info: SlotInfo }) {
  // Tiny corner badges. Stacked top-right.
  const badges: { text: string; color: string }[] = [];
  if (info.aim_row.present) badges.push({ text: "A", color: "bg-blue-600 text-white" });
  if (info.merc_row.present) badges.push({ text: "M", color: "bg-green-600 text-white" });
  if (info.engine_name) badges.push({ text: "★", color: "bg-yellow-500 text-wasteland-900" });
  if (badges.length === 0) return null;
  return (
    <span className="absolute top-0 right-0 flex flex-col gap-px">
      {badges.map((b, i) => (
        <span
          key={i}
          className={`text-[7px] leading-none px-px ${b.color} rounded-bl`}
          style={{ fontFamily: "ui-monospace, monospace" }}
        >
          {b.text}
        </span>
      ))}
    </span>
  );
}

function EngineSiteView({
  slots, selected, onSelect, allowFilled, laptopAimDisplayCap,
}: {
  slots: SlotInfo[];
  selected: number | null;
  onSelect: (slot: number) => void;
  allowFilled: boolean;
  laptopAimDisplayCap: number;
}) {
  const aimSlots = slots.filter((s) => s.aim_row.present);
  const mercSlots = slots.filter((s) => s.merc_row.present);
  const otherSlots = slots.filter(
    (s) => !s.aim_row.present && !s.merc_row.present && !s.is_empty
  );

  const [showOther, setShowOther] = useState(false);

  return (
    <div className="space-y-4">
      <Section
        title={`AIM hiring page — ${aimSlots.length} mugshots`}
        subtitle={`The game shows the first ${laptopAimDisplayCap} as clickable face thumbnails (8×5 grid × 3 pages)`}
      >
        <div className="grid gap-1" style={{ gridTemplateColumns: "repeat(8, minmax(0, 1fr))" }}>
          {aimSlots.slice(0, laptopAimDisplayCap).map((info) => (
            <SlotCell
              key={info.slot}
              info={info}
              selected={selected === info.slot}
              highlighted
              allowFilled={allowFilled}
              onSelect={() => onSelect(info.slot)}
            />
          ))}
        </div>
        {aimSlots.length > laptopAimDisplayCap && (
          <p className="mt-1 text-[10px] text-yellow-400">
            {aimSlots.length - laptopAimDisplayCap} extra AIM merc(s) won't get a face thumbnail on this grid. They still appear on the AIM members page and can be hired from there.
          </p>
        )}
      </Section>

      <Section title={`M.E.R.C. service — ${mercSlots.length} listed`} subtitle="In-game, Speck shows these one at a time">
        <ul className="text-xs divide-y divide-wasteland-800 border border-wasteland-800 rounded">
          {mercSlots.map((info) => (
            <li key={info.slot}>
              <button
                type="button"
                onClick={() => onSelect(info.slot)}
                className={`w-full px-2 py-1 text-left flex items-center justify-between hover:bg-wasteland-800 ${
                  selected === info.slot ? "bg-rust-500/20" : ""
                }`}
              >
                <span className="font-mono text-wasteland-200">
                  {info.merc_row.uiIndex ?? "—"} · slot {info.slot}
                </span>
                <span className="text-wasteland-100">
                  {info.merc_row.Name || info.profile_nickname || info.profile_name || "(unnamed)"}
                </span>
                <span className="text-[10px] text-wasteland-400 ml-3">
                  bio={info.merc_row.MercBioID ?? "—"}
                </span>
              </button>
            </li>
          ))}
          {mercSlots.length === 0 && (
            <li className="px-2 py-2 text-wasteland-500 italic">No M.E.R.C. rows.</li>
          )}
        </ul>
      </Section>

      <Section
        title={`Other occupied slots — ${otherSlots.length}`}
        subtitle="Filled MercProfiles entries with no AIM/MERC row (RPCs, NPCs, vehicles)"
      >
        <button
          type="button"
          onClick={() => setShowOther((v) => !v)}
          className="text-xs text-rust-400 underline-offset-2 hover:underline"
        >
          {showOther ? "Hide" : "Show"} {otherSlots.length} slot(s)
        </button>
        {showOther && (
          <ul className="mt-2 text-xs divide-y divide-wasteland-800 border border-wasteland-800 rounded">
            {otherSlots.map((info) => (
              <li key={info.slot}>
                <button
                  type="button"
                  onClick={() => onSelect(info.slot)}
                  className={`w-full px-2 py-1 text-left flex items-center justify-between hover:bg-wasteland-800 ${
                    selected === info.slot ? "bg-rust-500/20" : ""
                  }`}
                >
                  <span className="font-mono text-wasteland-200">slot {info.slot}</span>
                  <span className="text-wasteland-100">
                    {info.profile_nickname || info.profile_name || info.engine_name || "?"}
                  </span>
                  <span className="text-[10px] text-wasteland-400 ml-3">
                    {categoryLabel(info.category as SlotCategory)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

function Section({
  title, subtitle, children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border border-wasteland-800 rounded p-3 bg-wasteland-900/40">
      <header className="mb-2">
        <h3 className="text-xs font-semibold uppercase text-wasteland-300">{title}</h3>
        {subtitle && <p className="text-[10px] text-wasteland-500 mt-0.5">{subtitle}</p>}
      </header>
      {children}
    </section>
  );
}

function LegendChip({ color, label, title }: { color: string; label: string; title: string }) {
  return (
    <span className="flex items-center gap-1.5" title={title}>
      <span className={`inline-block w-3 h-3 rounded border-2 ${color}`} />
      <span className="font-semibold">{label}</span>
    </span>
  );
}

function Badge({ text, tooltip }: { text: string; tooltip: string }) {
  return (
    <span className="flex items-center gap-1.5" title={tooltip}>
      <span
        className="text-[10px] leading-none rounded bg-wasteland-700 text-wasteland-100 px-1 py-0.5 font-mono"
      >
        {text}
      </span>
    </span>
  );
}
