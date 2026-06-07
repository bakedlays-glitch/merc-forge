import { useEffect, useState } from "react";
import { buildDiagnosticReport, openLogFolder } from "../api/launcher";
import type { DiagnosticReport } from "../types/modpack";

interface Props {
  folder: string;
  activeSaveDir: string;       // e.g. "Profiles/AR"
  activeCampaignDisplay: string;
  onError: (msg: string) => void;
}

export function DiagnosticTab({
  folder,
  activeSaveDir,
  activeCampaignDisplay,
  onError,
}: Props) {
  const [report, setReport] = useState<DiagnosticReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [showNoise, setShowNoise] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const r = await buildDiagnosticReport(folder, activeSaveDir);
      setReport(r);
    } catch (e) {
      onError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder, activeSaveDir]);

  if (loading && !report) {
    return <p className="p-4 text-ja2-dim">Reading logs…</p>;
  }

  if (!report) {
    return (
      <p className="p-4 text-ja2-dim">
        No logs found yet. Launch <strong>{activeCampaignDisplay}</strong> at
        least once, then come back.
      </p>
    );
  }

  const realErrors = report.errors.filter((e) => !e.is_first_boot_noise);
  const visibleErrors = showNoise ? report.errors : realErrors;

  return (
    <div className="flex flex-col gap-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-ja2-accent">
            {activeCampaignDisplay}
          </h2>
          {report.last_launch_iso && (
            <p className="text-xs text-ja2-dim">
              Last launch: {report.last_launch_iso}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            className="ja2-btn text-xs"
            onClick={() => void openLogFolder(folder, activeSaveDir)}
          >
            Open log folder
          </button>
          <button className="ja2-btn text-xs" onClick={refresh}>
            Refresh
          </button>
        </div>
      </div>

      <section className="ja2-panel">
        <h3 className="text-base font-semibold text-ja2-accent mb-3">
          Mods loaded ({report.vfs_layers.length})
        </h3>
        {report.vfs_layers.length === 0 ? (
          <p className="text-sm text-ja2-dim">
            vfs.log was empty or unreadable.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {report.vfs_layers.map((layer, i) => (
              <li
                key={i}
                className="grid grid-cols-[1fr_auto_2fr] gap-3 text-sm py-1 border-b border-ja2-border last:border-b-0"
              >
                <span className="text-ja2-text">{layer.name}</span>
                <span className="text-xs text-ja2-dim self-center">
                  {layer.kind}
                </span>
                <span className="text-xs text-ja2-dim font-mono truncate">
                  {layer.path}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="ja2-panel">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-base font-semibold text-ja2-accent">
            INI errors ({realErrors.length} real
            {report.first_boot_noise_count > 0 &&
              ` + ${report.first_boot_noise_count} first-boot noise`}
            )
          </h3>
          {report.first_boot_noise_count > 0 && (
            <label className="flex items-center gap-2 text-xs text-ja2-dim cursor-pointer">
              <input
                type="checkbox"
                checked={showNoise}
                onChange={(e) => setShowNoise(e.target.checked)}
                className="w-3 h-3 accent-ja2-accent"
              />
              Show first-boot noise
            </label>
          )}
        </div>

        {visibleErrors.length === 0 ? (
          <p className="text-sm text-ja2-text">
            ✓ No errors. Logs are clean.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {visibleErrors.map((e, i) => (
              <li
                key={i}
                className={[
                  "text-xs border-l-4 pl-3 py-1",
                  e.kind === "out_of_range"
                    ? "border-ja2-accent"
                    : e.kind === "file_not_found"
                      ? "border-ja2-danger"
                      : e.is_first_boot_noise
                        ? "border-ja2-border"
                        : "border-ja2-dim",
                ].join(" ")}
              >
                <div className="text-ja2-text">
                  <strong>{e.section}</strong> · {e.key}
                </div>
                <div className="text-ja2-dim mt-0.5 font-mono">
                  {e.message}
                </div>
              </li>
            ))}
          </ul>
        )}

        {report.first_boot_noise_count > 0 && !showNoise && (
          <p className="text-xs text-ja2-dim mt-3 italic">
            First-boot noise (empty TOPTION_* keys) is normal — JA2 writes
            partial defaults on first launch, then reads them back and
            complains. A second launch is clean.
          </p>
        )}
      </section>

      <p className="text-xs text-ja2-dim">
        Logs are at <code className="text-ja2-text">{report.log_dir}</code>
      </p>
    </div>
  );
}
