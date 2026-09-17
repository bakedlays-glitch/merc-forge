import { categoryLabel, type SlotCategory } from "../lib/slotPicker";
import type { RosterEntry } from "../lib/schema";

/**
 * "Step 1: pick the source merc" card shared by the Copy (Duplicate)
 * and Cut (Move) flows. Two states:
 *
 *   - locked: the roster's context menu navigated here with ?from=<slot>
 *     — show the pre-picked merc + a Change link instead of making the
 *     user re-select from a dropdown (the bug a user hit).
 *   - dropdown: normal Step 1 select over the filled slots.
 */
export default function SourceMercCard({
  source,
  sourceLocked,
  sourceName,
  sourceClass,
  filled,
  stepTitle,
  placeholder,
  onChange,
}: {
  source: number | null;
  sourceLocked: boolean;
  sourceName: string;
  sourceClass: SlotCategory | null;
  filled: RosterEntry[];
  stepTitle: string;
  placeholder: string;
  onChange: (slot: number | null) => void;
}) {
  if (sourceLocked) {
    return (
      <section className="card">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase text-wasteland-500 mb-1">Source merc</div>
            <div className="text-wasteland-100">
              <span className="font-mono text-rust-400">Slot {source}</span>
              {" · "}
              <span className="font-medium">{sourceName}</span>
              {sourceClass && (
                <span className="badge bg-wasteland-700 text-wasteland-200 ml-2">{categoryLabel(sourceClass)}</span>
              )}
            </div>
          </div>
          <button
            type="button"
            className="text-xs text-rust-400 hover:underline underline-offset-2"
            onClick={() => onChange(null)}
          >
            Change
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="card">
      <h2 className="text-lg font-semibold mb-3">{stepTitle}</h2>
      <select
        className="input max-w-md"
        value={source ?? ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">{placeholder}</option>
        {filled.map((e) => (
          <option key={e.slot} value={e.slot}>
            Slot {e.slot}: {e.nickname ?? e.name}
          </option>
        ))}
      </select>
      {sourceClass && (
        <div className="mt-2 text-xs text-wasteland-400">
          Slot {source} is <span className="badge bg-wasteland-700 text-wasteland-200">{categoryLabel(sourceClass)}</span>
        </div>
      )}
    </section>
  );
}
