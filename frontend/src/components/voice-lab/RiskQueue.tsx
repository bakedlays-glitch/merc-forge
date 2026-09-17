import { useEffect, useMemo, useState } from "react";

import { voiceLabAudioUrl, type PortraitSheet } from "../../lib/api";
import type { VoiceBank, VoiceFinding } from "../../lib/voiceLabSchema";
import { describeVoiceLine, voiceLineKey } from "../../lib/voiceLabViewModel";

interface Props {
  findings: readonly VoiceFinding[];
  banks: readonly VoiceBank[];
  portraitSheet?: PortraitSheet;
  selectedLine: string | null;
  onOpen: (finding: VoiceFinding) => void;
}

const severityRail: Record<VoiceFinding["severity"], string> = {
  critical: "bg-red-500",
  high: "bg-rust-400",
  warning: "bg-amber-400",
  medium: "bg-yellow-500",
  low: "bg-wasteland-400",
  info: "bg-wasteland-600",
};

function displayProfile(bank: VoiceBank | undefined) {
  return bank?.profiles[0];
}

function FindingPortrait({ profile, sheet }: {
  profile: ReturnType<typeof displayProfile>;
  sheet?: PortraitSheet;
}) {
  const cell = sheet?.manifest.cells.find((candidate) => candidate.slot === profile?.profileId);
  if (profile && cell && sheet) {
    return (
      <span
        aria-label={`${profile.name} portrait`}
        role="img"
        className="h-11 w-10 shrink-0 border border-wasteland-700 bg-wasteland-950"
        style={{
          backgroundImage: `url(${sheet.blobUrl})`,
          backgroundPosition: `-${cell.x}px -${cell.y}px`,
          backgroundSize: `${sheet.manifest.sheet_w}px ${sheet.manifest.sheet_h}px`,
        }}
      />
    );
  }
  return (
    <span className="flex h-11 w-10 shrink-0 items-center justify-center border border-wasteland-700 bg-wasteland-950 font-mono text-xs text-wasteland-400" aria-hidden>
      {profile?.name.slice(0, 1).toUpperCase() ?? "?"}
    </span>
  );
}

function PlayButton({ assetId }: { assetId: string | undefined }) {
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    if (!assetId) return undefined;
    void voiceLabAudioUrl(assetId).then(
      (url) => { if (active) setAudioUrl(url); },
      () => { if (active) setFailed(true); },
    );
    return () => { active = false; };
  }, [assetId]);

  if (!assetId) return <span className="text-[10px] text-wasteland-500">No audio</span>;
  return (
    <button
      type="button"
      className="btn-ghost px-2 py-1 text-xs"
      disabled={!audioUrl || failed}
      onClick={() => { if (audioUrl) void new Audio(audioUrl).play(); }}
      aria-label="Play active audio"
      title={failed ? "Audio is unavailable" : "Play active audio"}
    >
      {failed ? "Unavailable" : audioUrl ? "Play" : "Loading…"}
    </button>
  );
}

export default function RiskQueue({ findings, banks, portraitSheet, selectedLine, onOpen }: Props) {
  const banksByIndex = useMemo(() => new Map(banks.map((bank) => [bank.voiceIndex, bank])), [banks]);
  if (findings.length === 0) {
    return <p className="border border-dashed border-wasteland-700 px-4 py-8 text-sm text-wasteland-400">No unresolved issues are saved for this merc. Run Analyze lines to check the recordings.</p>;
  }

  return (
    <ol className="border border-wasteland-700 divide-y divide-wasteland-700">
      {findings.map((finding) => {
        const bank = finding.voiceIndex === null ? undefined : banksByIndex.get(finding.voiceIndex);
        const profile = displayProfile(bank);
        const line = finding.lineId && finding.family
          ? bank?.lines.find((candidate) => voiceLineKey(candidate.family, candidate.lineId) === voiceLineKey(finding.family!, finding.lineId!))
          : undefined;
        const assetId = line?.audioWinner?.assetId ?? finding.assetIds[0];
        const description = line ? describeVoiceLine(line) : null;
        return (
          <li key={finding.stableKey} className={`relative bg-wasteland-900/30 px-4 py-3 ${
            finding.family && finding.lineId && selectedLine === voiceLineKey(finding.family, finding.lineId)
              ? "bg-rust-500/10"
              : ""
          }`}>
            <span className={`absolute inset-y-0 left-0 w-1 ${severityRail[finding.severity]}`} aria-hidden />
            <div className="flex gap-3 pl-1">
              <FindingPortrait profile={profile} sheet={portraitSheet} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <div>
                    <span className="font-medium text-wasteland-100">{profile?.name ?? "Unassigned voice bank"}</span>
                    {description && <span className="ml-2 text-[10px] text-wasteland-500">{description.category}</span>}
                  </div>
                  <span className="font-mono text-[10px] uppercase tracking-wide text-wasteland-300">{finding.severity}</span>
                </div>
                <p className="mt-1 text-sm text-wasteland-200">{finding.evidenceSummary}</p>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-wasteland-400">
                  <span>{description?.title ?? "Voice-set issue"}</span>
                  <span>Confidence {Math.round(finding.confidence * 100)}%</span>
                  <span>{finding.state.replaceAll("_", " ")}</span>
                  <PlayButton assetId={assetId} />
                  <button type="button" className="btn-primary px-2 py-1 text-xs" onClick={() => onOpen(finding)}>
                    Review this line
                  </button>
                </div>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
