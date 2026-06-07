import { ReactNode, useEffect, useRef, useState } from "react";

interface Props {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  /** If set, the user must type this string into a confirmation input. */
  typeToConfirm?: string;
  /** Disable the confirm button (e.g. while a mutation is pending). */
  busy?: boolean;
}

export default function ConfirmModal({
  open,
  title,
  body,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  onConfirm,
  onCancel,
  typeToConfirm,
  busy = false,
}: Props) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  // Escape closes; Enter confirms when the modal is non-destructive +
  // has no type-to-confirm gate. For destructive operations we focus
  // Cancel by default so a stray Enter doesn't fire the action; the
  // user has to consciously tab to Confirm.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onCancel();
        return;
      }
      if (e.key === "Enter" && !typeToConfirm && !destructive) {
        // Don't intercept Enter in a form or input the user happens to
        // have open elsewhere — but inside a modal the focused element
        // is one of our two buttons, so this is safe.
        e.preventDefault();
        if (!busy) onConfirm();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel, onConfirm, destructive, typeToConfirm, busy]);

  // Auto-focus: confirm button when safe (non-destructive, no
  // type-to-confirm); cancel button when destructive (matches the
  // SlotLockWarningModal pattern). TypeToConfirmInput focuses its own
  // text field via autoFocus, so we skip both buttons in that case.
  useEffect(() => {
    if (!open) return;
    if (typeToConfirm) return;
    const target = destructive ? cancelRef.current : confirmRef.current;
    // Slight delay lets the dialog mount before focus moves — without
    // this Chrome occasionally swallows the focus call on the first
    // open of a session.
    const id = window.setTimeout(() => target?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open, destructive, typeToConfirm]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      role="dialog"
      aria-modal="true"
      onClick={onCancel}
    >
      <div
        className="card max-w-md w-full mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-2">{title}</h2>
        <div className="text-sm text-wasteland-200 mb-4">{body}</div>
        {typeToConfirm && (
          <TypeToConfirmInput required={typeToConfirm} onMatch={onConfirm}>
            <button
              className={destructive ? "btn-primary bg-rust-600 hover:bg-rust-500" : "btn-primary"}
              type="submit"
              disabled={busy}
            >
              {confirmLabel}
            </button>
            <button type="button" className="btn-ghost" onClick={onCancel} disabled={busy}>
              {cancelLabel}
            </button>
          </TypeToConfirmInput>
        )}
        {!typeToConfirm && (
          <div className="flex gap-2 justify-end">
            <button
              ref={cancelRef}
              className="btn-ghost"
              onClick={onCancel}
              disabled={busy}
            >
              {cancelLabel}
            </button>
            <button
              ref={confirmRef}
              className={destructive ? "btn-primary bg-rust-600 hover:bg-rust-500" : "btn-primary"}
              onClick={onConfirm}
              disabled={busy}
            >
              {confirmLabel}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function TypeToConfirmInput({
  required,
  onMatch,
  children,
}: {
  required: string;
  onMatch: () => void;
  children: ReactNode;
}) {
  const [value, setValue] = useState("");
  const matches = value === required;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (matches) onMatch();
      }}
    >
      <label className="block text-sm text-wasteland-300 mb-1">
        Type <code className="font-mono text-rust-400">{required}</code> to confirm:
      </label>
      <input
        className="input mb-3"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoFocus
      />
      <div className="flex gap-2 justify-end">{children}</div>
    </form>
  );
}
