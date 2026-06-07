/**
 * Engine-faithful slot picker — TypeScript surface.
 *
 * Mirrors the sidecar's `mercwizard_core.slot_picker` module:
 *   - Each slot 0-254 carries a SlotInfo with tier (color), category
 *     (filter), is_empty, engine_name (from soldier profile type.h), and
 *     live AIM/MERC row data.
 *   - Tier semantics match `slotLocks.ts::tierStyle()` so existing color
 *     constants still apply.
 *   - Category is XML-driven first, slot-range fallback second.
 *
 * Replaces the static `classifySlot()` heuristic in `slotClass.ts`.
 */

import { useQuery } from "@tanstack/react-query";

import {
  getSlotPicker,
  type AimRowInfoApi,
  type MercRowInfoApi,
  type SlotInfoApi,
  type SlotPickerCategory,
  type SlotPickerResponseApi,
  type SlotPickerTier,
} from "./api";

export type SlotTier = SlotPickerTier;
export type SlotCategory = SlotPickerCategory;
export type AimRowInfo = AimRowInfoApi;
export type MercRowInfo = MercRowInfoApi;
export type SlotInfo = SlotInfoApi;
export type SlotPickerData = SlotPickerResponseApi;

/** Convenience indexer — null until query loads. */
export function findSlot(data: SlotPickerData | undefined, slot: number): SlotInfo | null {
  if (!data) return null;
  return data.slots[slot] ?? null;
}

/** React Query hook for the live slot picker map. 30s staleTime so
 * navigating between Create / Move / Roster doesn't refetch on every
 * mount.
 *
 * NB: writes MUST explicitly invalidate `["slot-picker"]` —
 * TanStack Query matches keys literally, so invalidating `["roster"]`
 * does not cascade. Create/Edit/Delete/Duplicate/Move/Import/Backups
 * restore + MercWizardRoster replace all do this. If you add a new
 * write handler that touches slot occupancy or AIM/MERC binding,
 * add an `invalidateQueries({queryKey: ["slot-picker"]})` call to its
 * onSuccess. Pre-fix the comment here claimed `["roster"]` churn
 * cascaded — bug-review finding E4. */
export function useSlotPicker(installId?: string) {
  return useQuery({
    queryKey: ["slot-picker", installId ?? "active"],
    queryFn: () => getSlotPicker(installId),
    staleTime: 30_000,
  });
}

/** Human-readable label for a category. Used in selection sidebars and
 * filter pill labels. */
export function categoryLabel(category: SlotCategory): string {
  switch (category) {
    case "aim":        return "AIM";
    case "merc":       return "M.E.R.C.";
    case "rpc":        return "RPC";
    case "npc":        return "NPC";
    case "locked":     return "Locked";
    case "unassigned": return "Unassigned";
    default:           return category;
  }
}

/** Build a short tooltip line summarizing what's at this slot. */
export function tooltipFor(info: SlotInfo): string[] {
  const lines: string[] = [];
  if (info.is_empty) {
    lines.push(`Empty slot ${info.slot}`);
  } else {
    const who = info.profile_nickname ?? info.profile_name ?? "?";
    lines.push(`${who} (slot ${info.slot})`);
  }
  if (info.engine_name) {
    lines.push(`Built-in character: ${info.engine_name}`);
  }
  if (info.aim_row.present) {
    lines.push("Listed on the AIM hiring page");
  }
  if (info.merc_row.present) {
    lines.push("Listed on Speck's M.E.R.C. service");
  }
  if (info.engine_role) {
    lines.push(info.engine_role);
  }
  return lines;
}
