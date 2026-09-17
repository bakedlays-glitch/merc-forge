import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getVoiceJob, getVoiceLineDetail, getVoiceWaveform, importVoiceSource, previewVoiceRecipe, saveVoiceRecipe, voiceLabAudioUrl } from "../../lib/api";
import { loadVoiceLineDraft, removeVoiceLineDraft, saveVoiceLineDraft } from "../../lib/voiceLabDrafts";
import type { ImportedVoiceAsset, TimeRange, VoiceBank, VoiceFinding, VoiceLine, VoiceSource } from "../../lib/voiceLabSchema";
import { buildRecipeDraft, describeVoiceLine, hasVoiceDraftChanges } from "../../lib/voiceLabViewModel";
import WaveformEditor from "./WaveformEditor";

interface Props {
  installId: string;
  bank: VoiceBank;
  line: VoiceLine;
  findings: readonly VoiceFinding[];
  onDeployRecipe?: (recipeId: string) => Promise<void>;
  openingDeployReview?: boolean;
}

function normalizedWords(text: string | null): string[] {
  return (text ?? "").trim().split(/\s+/).filter(Boolean);
}

function ComparedText({ text, other }: { text: string | null; other: string | null }) {
  if (text === null) return <span className="text-wasteland-500">Not available</span>;
  const otherWords = new Set(normalizedWords(other).map((word) => word.toLowerCase().replace(/[^a-z0-9']/g, "")));
  return <>{normalizedWords(text).map((word, index) => {
    const normalized = word.toLowerCase().replace(/[^a-z0-9']/g, "");
    const differs = other !== null && !otherWords.has(normalized);
    return <span key={`${index}-${word}`} className={differs ? "bg-rust-500/25 text-rust-200" : undefined}>{index ? " " : ""}{word}</span>;
  })}</>;
}

export default function LineWorkbench({ installId, bank, line, findings, onDeployRecipe, openingDeployReview = false }: Props) {
  const original = line.audioWinner;
  const audioRef = useRef<HTMLAudioElement>(null);
  const animationRef = useRef<number>();
  const restoredRef = useRef(false);
  const [source, setSource] = useState<VoiceSource | null>(original);
  const [imported, setImported] = useState<ImportedVoiceAsset | null>(null);
  const [cuts, setCuts] = useState<TimeRange[]>([]);
  const [subtitle, setSubtitle] = useState<string | null>(null);
  const [liveSubtitle, setLiveSubtitle] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [sourceUrl, setSourceUrl] = useState<string>();
  const [previewUrl, setPreviewUrl] = useState<string>();
  const [previewId, setPreviewId] = useState<string>();
  const [recipeId, setRecipeId] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [currentTimeMs, setCurrentTimeMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selectedRange, setSelectedRange] = useState<TimeRange | null>(null);
  const [auditionEndMs, setAuditionEndMs] = useState<number | null>(null);

  const identity = useMemo(() => ({ installId, voiceIndex: line.voiceIndex, family: line.family, lineId: line.lineId }), [installId, line.family, line.lineId, line.voiceIndex]);
  const detail = useQuery({ queryKey: ["voice-line-detail", line.voiceIndex, line.family, line.lineId], queryFn: () => getVoiceLineDetail(line.voiceIndex, line.family, line.lineId) });
  const waveform = useQuery({ queryKey: ["voice-waveform", source?.assetId], queryFn: () => getVoiceWaveform(source!.assetId), enabled: Boolean(source) });
  const preview = useQuery({ queryKey: ["voice-preview", previewId], queryFn: () => getVoiceJob(previewId!), enabled: Boolean(previewId), refetchInterval: (query) => ["complete", "failed", "cancelled"].includes(query.state.data?.status ?? "") ? false : 500 });

  useEffect(() => {
    restoredRef.current = false; setHydrated(false); setLiveSubtitle(null); setSubtitle(null); setImported(null); setCuts([]); setSource(original); setPreviewId(undefined); setRecipeId(undefined); setPreviewUrl(undefined); setCurrentTimeMs(0); setMessage(undefined);
    if (!original) return;
    const saved = loadVoiceLineDraft(window.localStorage, identity, original.sha256);
    if (!saved) return;
    const restoredSource: VoiceSource = saved.sourceAssetId === original.assetId ? original : {
      assetId: saved.sourceAssetId, installId, extension: ".ogg", sourceKind: "import", sizeBytes: 0,
      sha256: saved.sourceSha256, durationMs: 0,
    };
    restoredRef.current = true; setSource(restoredSource); setImported(restoredSource.sourceKind === "import" ? restoredSource : null); setCuts(saved.cuts); setSubtitle(saved.subtitle); setMessage("Saved draft restored.");
  }, [identity.family, identity.installId, identity.lineId, identity.voiceIndex, original?.assetId, original?.sha256]);

  useEffect(() => {
    if (!detail.isSuccess) return;
    setLiveSubtitle(detail.data.subtitle);
    if (!restoredRef.current) setSubtitle(detail.data.subtitle);
    setHydrated(true);
  }, [detail.data?.subtitle, detail.isSuccess, line.family, line.lineId]);

  const dirty = Boolean(original && source && hydrated && hasVoiceDraftChanges(line, liveSubtitle, { source, cuts, subtitle }));
  useEffect(() => {
    if (!original || !source || !hydrated) return;
    if (!dirty) { removeVoiceLineDraft(window.localStorage, identity); return; }
    saveVoiceLineDraft(window.localStorage, {
      version: 1, ...identity, originalAssetId: original.assetId, originalSha256: original.sha256,
      sourceAssetId: source.assetId, sourceSha256: source.sha256, sourceKind: source.sourceKind,
      cuts: [...cuts], subtitle,
    });
  }, [cuts, dirty, hydrated, identity, original, source, subtitle]);

  useEffect(() => { setPreviewId(undefined); setRecipeId(undefined); setPreviewUrl(undefined); }, [cuts, source?.assetId, subtitle]);
  useEffect(() => {
    let active = true; setSourceUrl(undefined);
    if (!source) return undefined;
    void voiceLabAudioUrl(source.assetId).then((url) => { if (active) setSourceUrl(url); });
    return () => { active = false; };
  }, [source?.assetId]);
  useEffect(() => {
    let active = true;
    if (preview.data?.status !== "complete" || !preview.data.previewAssetId) return undefined;
    void voiceLabAudioUrl(preview.data.previewAssetId).then((url) => { if (active) setPreviewUrl(url); });
    return () => { active = false; };
  }, [preview.data?.previewAssetId, preview.data?.status]);
  useEffect(() => {
    if (!playing) return undefined;
    const tick = () => { const audio = audioRef.current; if (!audio) return; const next = audio.currentTime * 1000; setCurrentTimeMs(next); if (auditionEndMs !== null && next >= auditionEndMs) { audio.pause(); setAuditionEndMs(null); return; } animationRef.current = requestAnimationFrame(tick); };
    animationRef.current = requestAnimationFrame(tick);
    return () => { if (animationRef.current !== undefined) cancelAnimationFrame(animationRef.current); };
  }, [auditionEndMs, playing]);

  const evidence = findings.filter((finding) => finding.voiceIndex === line.voiceIndex && finding.family === line.family && finding.lineId === line.lineId);
  const description = describeVoiceLine(line);
  if (!original) return <section className="border border-dashed border-wasteland-700 p-5 text-sm text-wasteland-400">This indexed line has no playable audio. Import support requires an existing target line, so there is nothing safe to edit here yet.</section>;

  async function chooseImport(file: File | undefined) {
    if (!file) return;
    try { const asset = await importVoiceSource(file); setImported(asset); setSource(asset); setMessage("Replacement audio added to this draft. The game is unchanged."); }
    catch { setMessage("That file could not be read. Choose an OGG, WAV, or MP3 audio file."); }
  }
  async function makePreview() {
    if (!source || !dirty) return;
    try { const recipe = await saveVoiceRecipe(buildRecipeDraft(line, source, cuts, subtitle)); const next = await previewVoiceRecipe(recipe.recipeId); setRecipeId(recipe.recipeId); setPreviewId(next.jobId); setMessage("Building the edited preview…"); }
    catch { setMessage("Preview could not be built. Check the source and cut times."); }
  }
  function seek(timeMs: number) { if (!audioRef.current) return; audioRef.current.currentTime = timeMs / 1000; setCurrentTimeMs(timeMs); }
  function playSource() { setAuditionEndMs(null); void audioRef.current?.play(); }
  function playSelection() { if (!selectedRange || !audioRef.current) return; seek(selectedRange.startMs); setAuditionEndMs(selectedRange.endMs); void audioRef.current.play(); }
  function resetDraft() { removeVoiceLineDraft(window.localStorage, identity); setSource(original); setImported(null); setCuts([]); setSubtitle(liveSubtitle); setPreviewId(undefined); setRecipeId(undefined); setMessage("Draft reset to the current game line."); }

  return <section aria-label="Line editor" className="border border-rust-500/60 bg-wasteland-900/60">
    <header className="border-b border-wasteland-700 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[10px] font-semibold uppercase tracking-[.18em] text-rust-400">4. Edit the line</p><h2 className="mt-1 text-xl font-semibold text-wasteland-100">{description.title}</h2><p className="mt-1 max-w-2xl text-xs text-wasteland-400">{description.explanation}</p></div><span className={`text-[10px] uppercase tracking-wide ${dirty ? "text-amber-300" : "text-wasteland-500"}`}>{dirty ? "Draft saved" : "No changes"}</span></div>
    </header>
    <div className="space-y-5 p-4">
      {evidence.length > 0 && <section aria-label="Why this line needs review" className="border-l-2 border-amber-400 bg-amber-400/5 px-3 py-2"><h3 className="text-xs font-semibold text-amber-200">Why this line needs review</h3>{evidence.map((item) => <p key={item.stableKey} className="mt-1 text-xs text-wasteland-300">{item.evidenceSummary}</p>)}</section>}
      <div className="grid gap-3 sm:grid-cols-2">
        <section className="border border-wasteland-700 bg-wasteland-950/40 p-3"><h3 className="text-[10px] font-semibold uppercase tracking-wide text-wasteland-400">Subtitle in the game</h3><p className="mt-2 min-h-12 text-sm leading-relaxed text-wasteland-100">{line.family === "battle" ? <span className="text-wasteland-500">Combat reactions do not use dialogue subtitles.</span> : <ComparedText text={liveSubtitle} other={detail.data?.transcription?.text ?? null} />}</p></section>
        <section className="border border-wasteland-700 bg-wasteland-950/40 p-3"><h3 className="text-[10px] font-semibold uppercase tracking-wide text-wasteland-400">What the recording says</h3><p className="mt-2 min-h-12 text-sm leading-relaxed text-wasteland-100">{detail.data?.transcription ? <ComparedText text={detail.data.transcription.text} other={liveSubtitle} /> : <span className="text-wasteland-500">No transcript yet. Run Analyze lines for this merc.</span>}</p>{detail.data?.transcription && <p className="mt-2 text-[10px] text-wasteland-500">Confidence {Math.round(detail.data.transcription.confidence * 100)}%</p>}</section>
      </div>
      <section><div className="mb-2 flex flex-wrap items-end justify-between gap-2"><div><h3 className="text-sm font-semibold text-wasteland-100">Trim the recording</h3><p className="text-xs text-wasteland-400">The pale line follows playback. Orange regions will be removed.</p></div><div className="flex gap-2"><button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={!sourceUrl} onClick={playSource} title="Play the source shown in the waveform">Play source</button><button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={!selectedRange || !sourceUrl} onClick={playSelection} title="Play only the selected orange region">Play selected region</button></div></div>
        {waveform.data ? <WaveformEditor peaks={waveform.data.peaks} durationMs={waveform.data.durationMs} cuts={cuts} currentTimeMs={currentTimeMs} onCutsChange={setCuts} onSeek={seek} onSelectionChange={setSelectedRange} /> : <p className="border border-dashed border-wasteland-700 p-5 text-sm text-wasteland-400">Loading the waveform…</p>}
        <audio ref={audioRef} src={sourceUrl} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => { setPlaying(false); setCurrentTimeMs(0); }} onTimeUpdate={(event) => setCurrentTimeMs(event.currentTarget.currentTime * 1000)} className="mt-3 h-9 w-full" controls aria-label="Current source playback" />
      </section>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <label className="block text-xs text-wasteland-300">Subtitle after this edit<textarea title="Words that will be displayed in game for this line" className="mt-1 min-h-20 w-full" value={subtitle ?? ""} disabled={line.family === "battle"} onChange={(event) => setSubtitle(event.target.value)} placeholder={line.family === "battle" ? "Combat sounds do not use dialogue subtitles." : "Enter the words shown in game."} /></label>
        <aside className="space-y-3 text-xs"><label className="block text-wasteland-300">Audio source<select title="Choose the recording used to build this line" className="mt-1 w-full" value={source?.assetId ?? ""} onChange={(event) => setSource(event.target.value === original.assetId ? original : imported)}><option value={original.assetId}>Current game audio</option>{imported && <option value={imported.assetId}>Imported replacement</option>}</select></label><label className="block text-wasteland-300">Replace with another recording<input title="Import an OGG, WAV, or MP3 recording as the replacement source" className="mt-1 block w-full text-xs" type="file" accept="audio/ogg,audio/wav,audio/mpeg,.ogg,.wav,.mp3" onChange={(event) => void chooseImport(event.target.files?.[0])} /></label></aside>
      </div>
      <div className="border-t border-wasteland-700 pt-4"><p className="text-[10px] font-semibold uppercase tracking-[.18em] text-rust-400">5. Preview, then deploy</p><div className="mt-2 flex flex-wrap items-center gap-3"><button type="button" className="btn-primary px-3 py-2 text-xs" disabled={!dirty || preview.data?.status === "running" || preview.data?.status === "queued"} onClick={() => void makePreview()} title={!dirty ? "Make a cut, change the subtitle, or choose replacement audio first." : "Build the exact edited result without changing the game."}>{preview.data && ["running", "queued"].includes(preview.data.status) ? "Building preview…" : "Preview changes"}</button><button type="button" title="Discard every saved draft change for this line" className="btn-ghost px-3 py-2 text-xs" disabled={!dirty} onClick={resetDraft}>Reset draft</button>{message && <span className="text-xs text-wasteland-400">{message}</span>}</div>{previewUrl && <div className="mt-3 border border-rust-500/40 bg-rust-500/5 p-3"><p className="mb-2 text-xs font-medium text-wasteland-100">Edited result</p><audio src={previewUrl} controls className="h-9 w-full" aria-label="Edited preview result" />{recipeId && onDeployRecipe && <button type="button" title="Open the exact file-change review before anything is written to the game" className="btn-secondary mt-3 px-3 py-2 text-xs" disabled={openingDeployReview} onClick={() => void onDeployRecipe(recipeId)}>{openingDeployReview ? "Checking files…" : "Review and deploy"}</button>}</div>}</div>
      <details className="border-t border-wasteland-800 pt-3 text-xs text-wasteland-400"><summary className="cursor-pointer text-wasteland-300">Advanced details and game identifiers</summary><dl className="mt-2 grid gap-1 font-mono text-[10px]"><div><dt className="inline text-wasteland-500">Game identifier: </dt><dd className="inline">{description.technicalId}</dd></div><div><dt className="inline text-wasteland-500">Voice set: </dt><dd className="inline">{bank.voiceIndex}</dd></div><div><dt className="inline text-wasteland-500">Current source: </dt><dd className="inline">{original.sourceKind} · {original.sha256.slice(0, 12)}…</dd></div>{Object.values(line.triggerByProfileId).map((trigger) => <div key={`${trigger.slot}-${trigger.name}`}><dt className="inline text-wasteland-500">Trigger {trigger.name}: </dt><dd className="inline">{trigger.meaning}</dd></div>)}</dl></details>
    </div>
  </section>;
}
