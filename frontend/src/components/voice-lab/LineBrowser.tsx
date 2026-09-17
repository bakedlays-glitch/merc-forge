import { useMemo, useState } from "react";

import type { VoiceFinding, VoiceLine } from "../../lib/voiceLabSchema";
import { describeVoiceLine, filterVoiceLines, voiceLineKey } from "../../lib/voiceLabViewModel";

interface Props { lines: readonly VoiceLine[]; findings: readonly VoiceFinding[]; selectedLine: string | null; onSelect: (line: VoiceLine) => void; }
const severityColor: Record<VoiceFinding["severity"], string> = { critical: "bg-red-500", high: "bg-rust-400", warning: "bg-amber-400", medium: "bg-yellow-500", low: "bg-wasteland-400", info: "bg-wasteland-600" };

export default function LineBrowser({ lines, findings, selectedLine, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const editableLines = useMemo(() => lines.filter((line) => line.family === "speech" || line.family === "battle"), [lines]);
  const visible = useMemo(() => filterVoiceLines(editableLines, query), [editableLines, query]);
  const issueByLine = useMemo(() => {
    const map = new Map<string, VoiceFinding>();
    for (const finding of findings) {
      if (!finding.family || !finding.lineId || finding.state !== "needs_review") continue;
      const key = voiceLineKey(finding.family, finding.lineId);
      if (!map.has(key)) map.set(key, finding);
    }
    return map;
  }, [findings]);
  return (
    <section aria-labelledby="line-browser-heading" className="border border-wasteland-700 bg-wasteland-950/35">
      <div className="border-b border-wasteland-700 p-3">
        <h3 id="line-browser-heading" className="text-xs font-semibold uppercase tracking-wide text-wasteland-200">3. Choose a line</h3>
        <p className="mt-1 text-[11px] text-wasteland-400">Every indexed spoken and combat line is listed here.</p>
        <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search line number or meaning…" title="Filter this merc's lines by number or plain-English use" className="mt-3 w-full border-wasteland-600 bg-wasteland-950 text-xs" />
        <p className="mt-2 text-[10px] text-wasteland-500">Showing {visible.length} of {editableLines.length}</p>
      </div>
      <ul className="max-h-[34rem] divide-y divide-wasteland-800 overflow-y-auto">
        {visible.map((line) => {
          const key = voiceLineKey(line.family, line.lineId); const description = describeVoiceLine(line); const issue = issueByLine.get(key); const active = selectedLine === key;
          return <li key={key}><button type="button" title={`Open line ${line.lineId}: ${description.explanation}`} onClick={() => onSelect(line)} className={`relative w-full px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-rust-400 ${active ? "bg-rust-500/15" : "hover:bg-wasteland-800/70"}`} aria-current={active ? "true" : undefined}>
            {issue && <span className={`absolute inset-y-0 left-0 w-1 ${severityColor[issue.severity]}`} aria-hidden />}
            <span className="block text-[10px] uppercase tracking-wide text-wasteland-500">{description.category}</span>
            <span className="mt-0.5 block text-sm text-wasteland-100">{description.title}</span>
            <span className="mt-1 flex items-center justify-between gap-2 font-mono text-[10px] text-wasteland-500"><span>Line {line.lineId}</span>{issue && <span className="uppercase text-amber-300">{issue.severity} issue</span>}</span>
          </button></li>;
        })}
      </ul>
      {visible.length === 0 && <p className="p-4 text-xs text-wasteland-400">No line matches that search.</p>}
    </section>
  );
}
