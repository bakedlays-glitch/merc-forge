import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getRoster, getSlot } from "../lib/api";
import type { Merc, RosterEntry } from "../lib/schema";

interface Props {
  /** Slot the new merc is being created at (preserved in the pre-filled merc) */
  targetSlot: number;
  /** Face index for the new merc (also preserved) */
  targetFaceIndex: number;
  /** Called when the user picks a source merc; receives the source's profile
   *  fields as a Merc with uiIndex/ubFaceIndex rewritten to the target. */
  onPick: (preFilledMerc: Merc) => void;
}

/**
 * Lets the user start a new merc from an existing one in the install.
 * Reads the source's MercProfiles row, rewrites uiIndex + ubFaceIndex to
 * the target slot, returns the result for the Create wizard to seed state.
 *
 * Use case: "I want Sulik but slightly different" — saves filling 100+ fields.
 */
export default function StartFromExistingPicker({
  targetSlot,
  targetFaceIndex,
  onPick,
}: Props) {
  const [search, setSearch] = useState("");
  const [sourceSlot, setSourceSlot] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const roster = useQuery({
    queryKey: ["roster"],
    queryFn: () => getRoster(),
    staleTime: 60 * 1000,
  });

  const matches = useMemo(() => {
    const all = (roster.data ?? []).filter((e: RosterEntry) => !e.is_empty);
    if (!search) return all.slice(0, 12);
    const q = search.toLowerCase();
    return all.filter((e) =>
      (e.name ?? "").toLowerCase().includes(q)
      || (e.nickname ?? "").toLowerCase().includes(q)
      || String(e.slot) === q
    ).slice(0, 12);
  }, [roster.data, search]);

  async function pickSource(slot: number) {
    setLoading(true);
    setSourceSlot(slot);
    try {
      const result = await getSlot(slot);
      const profile = result.profile;
      // Coerce string-typed XML values to the Merc shape. Mirrors Edit.tsx's
      // parseProfileToMerc pattern.
      const stringFields = new Set([
        "zName", "zNickname", "PANTS", "VEST", "SKIN", "HAIR",
        "biographyText", "additionalInfoText",
      ]);
      const out: Record<string, unknown> = {};
      for (const [key, raw] of Object.entries(profile)) {
        if (stringFields.has(key)) {
          out[key] = raw;
        } else {
          const n = parseInt(raw as string, 10);
          if (!Number.isNaN(n)) out[key] = n;
        }
      }
      // Rewrite identity to target slot
      out.uiIndex = targetSlot;
      out.ubFaceIndex = targetFaceIndex;
      // Wipe identity-bearing text so the user supplies fresh values
      out.zName = "";
      out.zNickname = "";
      onPick(out as unknown as Merc);
    } finally {
      setLoading(false);
    }
  }

  return (
    <details className="mb-4 rounded border border-wasteland-700 bg-wasteland-800/30">
      <summary className="cursor-pointer px-3 py-2 text-sm text-wasteland-200">
        Start from an existing merc (pre-fill all fields from another slot)
      </summary>
      <div className="p-3 space-y-2">
        <input
          type="text"
          className="input text-sm"
          placeholder="Search by name, nickname, or slot…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {roster.isLoading && (
          <p className="text-xs text-wasteland-500">Loading roster…</p>
        )}
        {roster.isError && (
          <p className="text-xs text-rust-400">Couldn't load roster.</p>
        )}
        {!roster.isLoading && matches.length === 0 && (
          <p className="text-xs text-wasteland-500">No mercs match.</p>
        )}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5 max-h-48 overflow-y-auto">
          {matches.map((e) => (
            <button
              key={e.slot}
              type="button"
              disabled={loading}
              onClick={() => pickSource(e.slot)}
              className={`text-left rounded border px-2 py-1.5 text-xs transition-colors ${
                sourceSlot === e.slot
                  ? "border-rust-500 bg-rust-500/10"
                  : "border-wasteland-700 hover:border-wasteland-500"
              }`}
            >
              <div className="font-medium truncate">{e.nickname ?? e.name ?? "?"}</div>
              <div className="text-wasteland-500 font-mono text-[10px]">slot {e.slot}</div>
            </button>
          ))}
        </div>
        <p className="text-xs text-wasteland-500">
          Stats, traits, personality, salary, voice, gear references all copy across. Name and
          nickname are cleared so you supply fresh values. Portrait still needs uploading.
        </p>
      </div>
    </details>
  );
}
