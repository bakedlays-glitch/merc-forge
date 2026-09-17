/**
 * Build provenance and the log folder — what someone needs when the app
 * is misbehaving and they are about to report it.
 *
 * The build timestamp answers the recurring "I edited the source but the
 * running app shows old text" question without digging through file
 * mtimes.
 */
import { useState } from "react";

// Mirrors sidecar/main.py's setup_logging. Duplicated rather than served,
// so a change to the log location there has to be repeated here.
const LOGS_PATH = "%APPDATA%\\MercWizard\\logs";

export default function AboutPanel({ sidecarVersion }: { sidecarVersion?: string }) {
  const built = __BUILD_TIMESTAMP__;
  const [copied, setCopied] = useState(false);

  let pretty: string = built;
  try {
    const d = new Date(built);
    if (!Number.isNaN(d.getTime())) pretty = d.toLocaleString();
  } catch {
    // Keep the raw ISO if locale formatting fails for any reason.
  }

  const minutesAgo = (() => {
    try {
      const d = new Date(built);
      if (Number.isNaN(d.getTime())) return null;
      const mins = Math.floor((Date.now() - d.getTime()) / 60_000);
      if (mins < 1) return "just now";
      if (mins < 60) return `${mins} min ago`;
      const hours = Math.floor(mins / 60);
      if (hours < 24) return `${hours} hr ago`;
      const days = Math.floor(hours / 24);
      return `${days} day${days === 1 ? "" : "s"} ago`;
    } catch {
      return null;
    }
  })();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(LOGS_PATH);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable — the path is on screen to copy by hand.
    }
  };

  return (
    <section className="card space-y-3">
      <div>
        <h2 className="text-lg font-semibold">About</h2>
        <p className="text-sm text-wasteland-300">
          Merc Forge v1.0.0-beta.4 — open source under the MIT license.
        </p>
      </div>

      <div className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
        <div className="text-wasteland-500">Frontend built</div>
        <div className="text-wasteland-200 font-mono">
          {pretty}
          {minutesAgo && (
            <span className="text-wasteland-500 font-sans ml-2">({minutesAgo})</span>
          )}
        </div>
        <div className="text-wasteland-500">Sidecar version</div>
        <div className="text-wasteland-200 font-mono">
          {sidecarVersion ?? <span className="text-wasteland-500">unknown</span>}
        </div>
        <div className="text-wasteland-500">Logs</div>
        <div className="flex items-center gap-2">
          <code className="min-w-0 flex-1 truncate font-mono text-wasteland-200">
            {LOGS_PATH}
          </code>
          <button
            type="button"
            onClick={copy}
            className="shrink-0 rounded border border-wasteland-700 bg-wasteland-800 px-2 py-0.5 hover:border-rust-500 hover:bg-wasteland-700"
            title="Copy the path, then paste it into File Explorer or Win+R."
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>
      </div>
      <p className="text-[11px] text-wasteland-500">
        The log folder holds <code>sidecar.log</code> (Python) and{" "}
        <code>shell_rCURRENT.log</code> (Tauri). Both are worth attaching to a
        bug report.
      </p>
    </section>
  );
}
