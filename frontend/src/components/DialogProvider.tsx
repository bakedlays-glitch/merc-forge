/**
 * App-wide imperative dialog service — the single owner of "ask the user
 * to confirm / type a value" so nothing has to fall back to the native
 * window.confirm()/prompt() (which break the app's dark theme, block the
 * render thread, and skip the destructive-defaults-to-Cancel safety the
 * app's own ConfirmModal already implements).
 *
 * Usage:
 *   const { confirm, prompt } = useDialog();
 *   if (await confirm({ title: "Wipe sector?", body: "…", destructive: true })) { … }
 *   const name = await prompt({ title: "Save a copy as", label: "File name" });
 *
 * Each call returns a Promise that resolves when the user answers, so a
 * native `if (window.confirm(x)) { … }` becomes `if (await confirm(x)) { … }`
 * with the enclosing handler marked async — a near 1:1 migration.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import ConfirmModal from "./ConfirmModal";

export interface ConfirmOptions {
  title?: string;
  body: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  /** Require the user to type this exact string before Confirm enables. */
  typeToConfirm?: string;
  /** Run a mutation before the dialog resolves; it stays mounted while pending. */
  onConfirm?: () => Promise<unknown> | void;
}

export interface PromptOptions {
  title?: string;
  body?: ReactNode;
  /** Field label above the input. */
  label?: string;
  defaultValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Return an error string to block submission, or null when valid. */
  validate?: (value: string) => string | null;
}

interface DialogApi {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  prompt: (opts: PromptOptions) => Promise<string | null>;
}

type Pending =
  | { kind: "confirm"; opts: ConfirmOptions; resolve: (v: boolean) => void }
  | { kind: "prompt"; opts: PromptOptions; resolve: (v: string | null) => void };

const DialogContext = createContext<DialogApi | null>(null);

export function DialogProvider({ children }: { children: ReactNode }) {
  // A FIFO queue so a second request while one is open waits its turn
  // rather than clobbering the first (which would leak its promise).
  const [queue, setQueue] = useState<Pending[]>([]);
  const queueRef = useRef(queue);
  const settledRef = useRef(false);
  const busyRef = useRef(false);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  queueRef.current = queue;

  const enqueue = useCallback((p: Pending) => {
    setQueue((q) => [...q, p]);
  }, []);

  const confirm = useCallback(
    (opts: ConfirmOptions) =>
      new Promise<boolean>((resolve) => enqueue({ kind: "confirm", opts, resolve })),
    [enqueue],
  );
  const prompt = useCallback(
    (opts: PromptOptions) =>
      new Promise<string | null>((resolve) => enqueue({ kind: "prompt", opts, resolve })),
    [enqueue],
  );

  const settle = useCallback((value: boolean | string | null) => {
    if (settledRef.current) return;
    const head = queueRef.current[0];
    if (!head) return;
    settledRef.current = true;
    // Narrowing by kind keeps the resolver's parameter type honest.
    if (head.kind === "confirm") head.resolve(Boolean(value));
    else head.resolve(value === false ? null : (value as string | null));
    setQueue((q) => q.slice(1));
    setConfirmBusy(false);
    setConfirmError(null);
  }, []);

  const active = queue[0] ?? null;

  useEffect(() => {
    settledRef.current = false;
    busyRef.current = false;
    setConfirmBusy(false);
    setConfirmError(null);
  }, [active]);

  const confirmActive = async () => {
    const head = queueRef.current[0];
    if (!head || head.kind !== "confirm" || busyRef.current || settledRef.current) return;
    if (!head.opts.onConfirm) {
      settle(true);
      return;
    }
    busyRef.current = true;
    setConfirmBusy(true);
    setConfirmError(null);
    try {
      await head.opts.onConfirm();
      settle(true);
    } catch {
      busyRef.current = false;
      setConfirmBusy(false);
      setConfirmError("The action could not be completed. Try again or cancel.");
    }
  };

  const cancelActive = () => {
    if (!busyRef.current) settle(false);
  };

  const api = useMemo<DialogApi>(() => ({ confirm, prompt }), [confirm, prompt]);

  return (
    <DialogContext.Provider value={api}>
      {children}
      {active?.kind === "confirm" && (
        <ConfirmModal
          open
          title={active.opts.title ?? "Are you sure?"}
          body={active.opts.body}
          confirmLabel={active.opts.confirmLabel}
          cancelLabel={active.opts.cancelLabel}
          destructive={active.opts.destructive}
          typeToConfirm={active.opts.typeToConfirm}
          busy={confirmBusy}
          error={confirmError}
          onConfirm={() => { void confirmActive(); }}
          onCancel={cancelActive}
        />
      )}
      {active?.kind === "prompt" && (
        <PromptModal
          opts={active.opts}
          onSubmit={(v) => settle(v)}
          onCancel={() => settle(null)}
        />
      )}
    </DialogContext.Provider>
  );
}

export function useDialog(): DialogApi {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error("useDialog must be used within a DialogProvider");
  return ctx;
}

function PromptModal({
  opts,
  onSubmit,
  onCancel,
}: {
  opts: PromptOptions;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(opts.defaultValue ?? "");
  const error = opts.validate ? opts.validate(value) : null;

  const submit = () => {
    if (error) return;
    onSubmit(value);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      role="dialog"
      aria-modal="true"
      data-app-dialog="1"
      onClick={onCancel}
    >
      <div className="card max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold mb-2">{opts.title ?? "Enter a value"}</h2>
        {opts.body && <div className="text-sm text-wasteland-200 mb-3">{opts.body}</div>}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          {opts.label && (
            <label className="block text-sm text-wasteland-300 mb-1">{opts.label}</label>
          )}
          <input
            className="input"
            value={value}
            placeholder={opts.placeholder}
            autoFocus
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onCancel();
            }}
          />
          {error && <p className="mt-1 text-xs text-rust-300">{error}</p>}
          <div className="mt-4 flex gap-2 justify-end">
            <button type="button" className="btn-ghost" onClick={onCancel}>
              {opts.cancelLabel ?? "Cancel"}
            </button>
            <button type="submit" className="btn-primary" disabled={!!error}>
              {opts.confirmLabel ?? "OK"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
