import { useEffect, useRef, useState } from "react";

import { suppressLockTier, tierStyle, type SlotLockInfo } from "../lib/slotLocks";

interface Props {
  /** The lock info for the slot the user is about to write to. */
  lock: SlotLockInfo;
  /** The action the user is taking — used in the prompt copy. */
  action: "create" | "import" | "move" | "duplicate";
  onConfirm: () => void;
  onCancel: () => void;
}

const ACTION_VERB: Record<Props["action"], string> = {
  create: "create a merc at",
  import: "import to",
  move: "move a merc to",
  duplicate: "duplicate a merc to",
};

/** Confirmation modal shown before writing to a slot that's named in engine
 * source. The user must click "Continue" to proceed; "Cancel" aborts. A
 * "Don't show again for this tier" checkbox writes a localStorage flag that
 * suppresses future modals for the same tier. */
export function SlotLockWarningModal({ lock, action, onConfirm, onCancel }: Props) {
  const [suppress, setSuppress] = useState(false);
  const style = tierStyle(lock.tier);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  // Match ConfirmModal accessibility: Esc cancels, focus moves to the
  // Cancel button so the safer action is the first keyboard target.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handler);
    // Defer focus so the button has mounted.
    const t = setTimeout(() => cancelButtonRef.current?.focus(), 0);
    return () => {
      window.removeEventListener("keydown", handler);
      clearTimeout(t);
    };
  }, [onCancel]);

  const handleContinue = () => {
    if (suppress) suppressLockTier(lock.tier);
    onConfirm();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="slot-lock-warning-title"
      onClick={onCancel}
    >
      <div
        className="card max-w-lg w-full space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <div className={`text-2xl ${lock.tier === "locked" ? "text-red-400" : lock.tier === "quest_bound" ? "text-violet-400" : "text-yellow-400"}`}>
            {lock.tier === "locked" ? "🔒" : "⚠"}
          </div>
          <div className="flex-1 min-w-0">
            <h2 id="slot-lock-warning-title" className="text-lg font-bold">
              Slot {lock.slot} is {style.label.toLowerCase()}
            </h2>
            {lock.name && (
              <div className="text-sm text-wasteland-300 mt-0.5">
                Engine source name: <span className="font-mono text-rust-400">{lock.name}</span>
              </div>
            )}
          </div>
        </div>

        <p className="text-sm text-wasteland-200">
          You're about to {ACTION_VERB[action]} slot{" "}
          <span className="font-mono text-rust-400">{lock.slot}</span>.
        </p>

        {lock.role && (
          <div className="rounded border border-wasteland-700 bg-wasteland-800 p-3 text-sm">
            <div className="text-xs text-wasteland-400 uppercase mb-1">What this slot is</div>
            <div className="text-wasteland-100">{lock.role}</div>
          </div>
        )}

        <div className="rounded border border-wasteland-700 bg-wasteland-800 p-3 text-sm">
          <div className="text-xs text-wasteland-400 uppercase mb-1">Why this matters</div>
          <div className="text-wasteland-200">{style.description}</div>
        </div>

        {lock.tier === "locked" && (
          <div className="rounded border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">
            <strong>This is a high-risk slot.</strong> Continuing may break the
            main game or have the engine overwrite your work the moment the
            player triggers the relevant in-game event. Only proceed if you
            know exactly what you're doing.
          </div>
        )}

        <label className="flex items-center gap-2 text-sm text-wasteland-300 cursor-pointer">
          <input
            type="checkbox"
            checked={suppress}
            onChange={(e) => setSuppress(e.target.checked)}
            className="rounded"
          />
          <span>
            Don't show this warning again for {style.label.toLowerCase()} slots
          </span>
        </label>

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-wasteland-700">
          <button type="button" className="btn-ghost" ref={cancelButtonRef} onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className={`btn-primary ${lock.tier === "locked" ? "bg-red-600 hover:bg-red-500" : lock.tier === "quest_bound" ? "bg-violet-600 hover:bg-violet-500" : ""}`}
            onClick={handleContinue}
          >
            Continue anyway
          </button>
        </div>
      </div>
    </div>
  );
}
