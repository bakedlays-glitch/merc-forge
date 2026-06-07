/**
 * Slot-lock classification — frontend mirror of `sidecar/mercwizard_core/slot_locks.py`.
 *
 * The backend ships the full 0-254 map via `GET /slots/locks`. We fetch it once
 * via React Query and use the data for:
 *   - Color-coding the Roster tiles + SlotPicker grid
 *   - Pre-write warning modal in Create/Import/Move/Duplicate
 *
 * "Don't show again" per-tier suppression lives in localStorage so the
 * preference survives across app launches but is per-machine.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getSlotLocks } from "./api";
import { useSlotPicker } from "./slotPicker";

export type SlotLockTier = "safe" | "vanilla_overwrite" | "quest_bound" | "locked";

export interface SlotLockInfo {
  slot: number;
  tier: SlotLockTier;
  name: string | null;
  role: string | null;
}

const SUPPRESS_KEY_PREFIX = "mw_slot_lock_suppress_";

/** Returns true if the user has previously checked "Don't show again" for this tier. */
export function isLockSuppressed(tier: SlotLockTier): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(SUPPRESS_KEY_PREFIX + tier) === "1";
  } catch {
    return false;
  }
}

/** Suppress future warnings for the given tier. */
export function suppressLockTier(tier: SlotLockTier): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(SUPPRESS_KEY_PREFIX + tier, "1");
  } catch {
    // localStorage full / disabled — silent fallback
  }
}

/** Re-enable warnings for a tier (debug / settings UI). */
export function unsuppressLockTier(tier: SlotLockTier): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(SUPPRESS_KEY_PREFIX + tier);
  } catch {
    // ignore
  }
}

/** React Query hook returning the full 0-254 lock map. Cached aggressively
 * since the data is static (compiled from engine source). */
export function useSlotLocks() {
  return useQuery({
    queryKey: ["slot-locks"],
    queryFn: getSlotLocks,
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

/** Lookup helper for non-React contexts (or when you already have the map
 * from useSlotLocks). Returns null if the map isn't loaded yet. */
export function findLock(
  locks: SlotLockInfo[] | undefined,
  slot: number,
): SlotLockInfo | null {
  if (!locks) return null;
  return locks.find((l) => l.slot === slot) ?? null;
}

/** Reusable guard hook: wrap a write-action callback in `guard(slot, callback)`.
 *
 * Sources tier data from the engine-faithful `/slots/picker` endpoint so it
 * sees live AIM/MERC row presence — same view the SlotPicker UI uses. The
 * legacy `/slots/locks` endpoint stays alive for older callers but isn't
 * touched here anymore.
 *
 * Behavior:
 *   - If target slot is `safe` → callback runs immediately.
 *   - If target slot is risky AND user has suppressed that tier → callback runs.
 *   - Otherwise → modal appears; callback runs on Confirm, drops on Cancel.
 */
export function useSlotLockGuard() {
  const picker = useSlotPicker();
  const [pending, setPending] = useState<
    { lock: SlotLockInfo; callback: () => void } | null
  >(null);

  const guard = (slot: number, callback: () => void): void => {
    const info = picker.data?.slots[slot];
    const tier = (info?.tier ?? "safe") as SlotLockTier;
    if (!info || tier === "safe" || isLockSuppressed(tier)) {
      callback();
      return;
    }
    setPending({
      lock: {
        slot,
        tier,
        name: info.engine_name,
        role: info.engine_role,
      },
      callback,
    });
  };

  const confirm = () => {
    if (pending) pending.callback();
    setPending(null);
  };

  const cancel = () => setPending(null);

  return { guard, pending, confirm, cancel };
}

/** Tier → display label + Tailwind classes. Centralized so Roster and
 * SlotPicker render consistently. Use `tileClass` for the full tile look
 * (border + background tint together — applied as a single Tailwind string)
 * and `badgeClass` for the small constant-name chip. */
export function tierStyle(tier: SlotLockTier): {
  label: string;
  borderClass: string;       // legacy — kept for back-compat; use tileClass
  tileClass: string;         // border-2 + bg-tint, fully visible
  badgeClass: string;        // chip background
  dotClass: string;          // small color dot indicator
  description: string;
} {
  switch (tier) {
    case "locked":
      return {
        label: "Locked",
        borderClass: "border-red-500",
        tileClass: "border-2 border-red-500 bg-red-500/15",
        badgeClass: "bg-red-500 text-white font-bold",
        dotClass: "bg-red-500",
        description:
          "Engine writes to this slot or main-quest scripts reference it by name. Overwriting causes silent breakage.",
      };
    case "quest_bound":
      return {
        label: "Quest-bound",
        borderClass: "border-violet-500",
        tileClass: "border-2 border-violet-500 bg-violet-500/15",
        badgeClass: "bg-violet-500 text-white font-bold",
        dotClass: "bg-violet-500",
        description:
          "Named in engine source — quest scripts and dialogue call this slot by name. Overwriting redirects the quest to your replacement merc.",
      };
    case "vanilla_overwrite":
      return {
        label: "Overwrite base character",
        borderClass: "border-yellow-400",
        tileClass: "border-2 border-yellow-400 bg-yellow-400/15",
        badgeClass: "bg-yellow-400 text-wasteland-900 font-bold",
        dotClass: "bg-yellow-400",
        description:
          "Base-game AIM or M.E.R.C. slot (e.g. Bull, Shadow, Fox, Buns). "
          + "Saving here REPLACES the original character; your custom merc "
          + "inherits their place on the in-game laptop hiring site. The "
          + "original is gone for this install until you restore the .mwbak.",
      };
    case "safe":
    default:
      return {
        label: "1.13 Expansion",
        borderClass: "border-wasteland-700",
        tileClass: "border border-wasteland-700 bg-wasteland-800",
        badgeClass: "bg-wasteland-700 text-wasteland-300",
        dotClass: "bg-wasteland-500",
        description:
          "Unnamed in engine source — 1.13 expansion territory, free for mod content. AIM/MERC visibility depends entirely on XML wiring (AIMAvailability.xml / MercAvailability.xml).",
      };
  }
}
