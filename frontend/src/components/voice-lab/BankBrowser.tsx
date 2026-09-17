import { useMemo, useState } from "react";

import type { VoiceBank } from "../../lib/voiceLabSchema";

interface Props {
  banks: readonly VoiceBank[];
  selectedBank: number | null;
  indexedBanks: ReadonlySet<number>;
  indexingBank: number | null;
  onSelect: (voiceIndex: number) => void;
}

export default function BankBrowser({ banks, selectedBank, indexedBanks, indexingBank, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return banks;
    return banks.filter((bank) => bank.profiles.some((profile) =>
      `${profile.name} ${profile.nickname}`.toLowerCase().includes(needle)
    ) || String(bank.voiceIndex).includes(needle));
  }, [banks, query]);

  return (
    <nav aria-label="Mercs with voice lines" className="border border-wasteland-700 bg-wasteland-900/40">
      <div className="border-b border-wasteland-700 p-3">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-wasteland-200">1. Choose a merc</h2>
        <p className="mt-1 text-[11px] leading-relaxed text-wasteland-400">Pick the character whose recorded lines you want to review.</p>
        <label className="mt-3 block">
          <span className="sr-only">Search mercs</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search mercs…" title="Filter the merc list by name" className="input w-full border-wasteland-600 bg-wasteland-950 font-sans text-sm" />
        </label>
      </div>
      {banks.length === 0 ? (
        <p className="px-3 py-5 text-xs text-wasteland-400">Index the game files to find merc voice sets.</p>
      ) : visible.length === 0 ? (
        <p className="px-3 py-5 text-xs text-wasteland-400">No merc matches that search.</p>
      ) : (
        <ul className="max-h-[62vh] divide-y divide-wasteland-800 overflow-y-auto">
          {visible.map((bank) => {
            const active = bank.voiceIndex === selectedBank;
            const indexed = indexedBanks.has(bank.voiceIndex);
            const indexing = bank.voiceIndex === indexingBank;
            const names = bank.profiles.map((profile) => profile.name).filter(Boolean);
            return (
              <li key={bank.voiceIndex}>
                <button type="button" title={`Open ${names[0] || "this voice set"}'s complete line list`} className={`w-full px-3 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-rust-400 ${active ? "bg-rust-500/15" : "hover:bg-wasteland-800/80"}`} aria-current={active ? "page" : undefined} onClick={() => onSelect(bank.voiceIndex)}>
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium text-wasteland-100">{names[0] || "Unassigned voice set"}</span>
                    <span className="shrink-0 text-[10px] text-wasteland-500">{indexing ? "Indexing…" : indexed ? `${bank.lines.filter((line) => line.family !== "dialogue_edt").length} lines` : "Ready to index"}</span>
                  </span>
                  {names.length > 1 && <span className="mt-1 block text-[10px] text-amber-300">Shared with {names.slice(1).join(", ")}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}
