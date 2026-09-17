import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import BankBrowser from "../components/voice-lab/BankBrowser";
import DeployDialog from "../components/voice-lab/DeployDialog";
import HistoryPanel from "../components/voice-lab/HistoryPanel";
import LineBrowser from "../components/voice-lab/LineBrowser";
import LineWorkbench from "../components/voice-lab/LineWorkbench";
import RiskQueue from "../components/voice-lab/RiskQueue";
import {
  cancelVoiceJob, deployVoicePlan, formatApiError, getRosterPortraitSheet, getVoiceBankCatalog, getVoiceBanks,
  getVoiceDeploymentHistory, getVoiceFindings, getVoiceJob, getVoiceLabStatus,
  preflightVoiceRecipe, startVoiceAnalysis, startVoiceScan, undoVoiceDeployment,
} from "../lib/api";
import type { VoiceDeployPlan, VoiceLabJob } from "../lib/voiceLabSchema";
import { isActiveVoiceLabJob, pollVoiceLabJob, pollVoiceLabJobs, reconcileVoiceLabScanJob, refreshVoiceLabAfterMutation } from "../lib/voiceLabPolling";
import { filterAndSortFindings, findHighestRiskLine, mergeVoiceBankCatalog, normalizeVoiceLabSelection, voiceLineKey } from "../lib/voiceLabViewModel";

const stages = ["Choose merc", "Analyze lines", "Review issue", "Edit", "Preview", "Deploy"];

export default function VoiceLab() {
  const queryClient = useQueryClient(); const navigate = useNavigate(); const [searchParams, setSearchParams] = useSearchParams();
  const [job, setJob] = useState<VoiceLabJob | null>(null); const [operationError, setOperationError] = useState<unknown>(null);
  const [scanJobs, setScanJobs] = useState<Record<string, VoiceLabJob>>({});
  const [deployPlan, setDeployPlan] = useState<VoiceDeployPlan | null>(null); const [refreshError, setRefreshError] = useState<unknown>(null); const [refreshingInventory, setRefreshingInventory] = useState(false);
  const [indexingBanks, setIndexingBanks] = useState<Set<number>>(() => new Set()); const analysisBank = useRef<number | null>(null);
  const mounted = useRef(true);
  const requestedBankScans = useRef(new Set<number>()); const scanBankByJob = useRef(new Map<string, number>());

  const status = useQuery({ queryKey: ["voice-lab-status"], queryFn: () => getVoiceLabStatus() });
  const catalog = useQuery({ queryKey: ["voice-lab-catalog"], queryFn: () => getVoiceBankCatalog(), staleTime: 30_000 });
  const banks = useQuery({ queryKey: ["voice-lab-banks"], queryFn: () => getVoiceBanks() });
  const history = useQuery({ queryKey: ["voice-lab-deployments"], queryFn: () => getVoiceDeploymentHistory() });
  const indexedBanks = useMemo(() => new Set((banks.data ?? []).map((bank) => bank.voiceIndex)), [banks.data]);
  const visibleBanks = useMemo(() => mergeVoiceBankCatalog(catalog.data ?? [], banks.data ?? []), [catalog.data, banks.data]);

  const rawBank = searchParams.get("bank"); const rawProfile = searchParams.get("profile");
  const profileBank = rawProfile !== null && Number.isInteger(Number(rawProfile)) ? visibleBanks.find((bank) => bank.profiles.some((profile) => profile.profileId === Number(rawProfile)))?.voiceIndex ?? null : null;
  const requestedSelection = { bank: rawBank !== null && Number.isInteger(Number(rawBank)) ? Number(rawBank) : profileBank, line: searchParams.get("line") };
  const selection = normalizeVoiceLabSelection(requestedSelection, visibleBanks);
  const activeScanJobs = useMemo(() => Object.values(scanJobs).filter(isActiveVoiceLabJob), [scanJobs]);
  const activeScanJobKey = useMemo(() => activeScanJobs.map((scanJob) => `${scanJob.jobId}:${scanJob.status}`).sort().join("|"), [activeScanJobs]);
  const selectedScanJob = activeScanJobs.find((scanJob) => scanBankByJob.current.get(scanJob.jobId) === selection.bank) ?? null;
  const displayJob = job?.kind === "analysis" && isActiveVoiceLabJob(job) ? job : selectedScanJob ?? activeScanJobs[0] ?? job;
  const indexingBank = selection.bank !== null && indexingBanks.has(selection.bank)
    ? selection.bank
    : indexingBanks.values().next().value ?? null;
  const selectedBank = visibleBanks.find((bank) => bank.voiceIndex === selection.bank);
  const selectedIndexed = selection.bank !== null && indexedBanks.has(selection.bank);
  const findings = useQuery({ queryKey: ["voice-lab-findings"], queryFn: () => getVoiceFindings(), enabled: selectedIndexed });
  const portraits = useQuery({ queryKey: ["roster-portrait-sheet", "voice-lab"], queryFn: () => getRosterPortraitSheet({ size: "bigface" }), staleTime: Infinity, enabled: selectedIndexed });
  const selectedLine = selectedBank?.lines.find((line) => voiceLineKey(line.family, line.lineId) === selection.line);
  const bankFindings = filterAndSortFindings(findings.data ?? [], { bank: selection.bank ?? undefined, states: ["needs_review"] });

  function updateSelection(bank: number, line: string | null = null) {
    const next = new URLSearchParams(searchParams); next.set("bank", String(bank)); next.delete("profile");
    if (line) next.set("line", line); else next.delete("line"); setSearchParams(next);
  }

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const clearRequestedBankScan = useCallback((voiceIndex: number) => {
    requestedBankScans.current.delete(voiceIndex);
    setIndexingBanks((current) => {
      if (!current.has(voiceIndex)) return current;
      const next = new Set(current); next.delete(voiceIndex); return next;
    });
  }, []);

  const completeScanJob = useCallback((latest: VoiceLabJob) => {
    const completedScanBank = scanBankByJob.current.get(latest.jobId) ?? null;
    if (completedScanBank !== null) {
      clearRequestedBankScan(completedScanBank);
      scanBankByJob.current.delete(latest.jobId);
    }
    setScanJobs((current) => {
      if (!(latest.jobId in current)) return current;
      const next = { ...current }; delete next[latest.jobId]; return next;
    });
    setJob((current) => current?.jobId === latest.jobId ? latest : current);
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ["voice-lab-status"] }),
      queryClient.invalidateQueries({ queryKey: ["voice-lab-banks"] }),
      queryClient.invalidateQueries({ queryKey: ["voice-lab-findings"] }),
      queryClient.invalidateQueries({ queryKey: ["voice-line-detail"] }),
    ]).catch(setOperationError);
  }, [clearRequestedBankScan, queryClient]);

  const ensureBankIndexed = useCallback((voiceIndex: number) => {
    if (requestedBankScans.current.has(voiceIndex)) return;
    requestedBankScans.current.add(voiceIndex);
    setIndexingBanks((current) => new Set(current).add(voiceIndex));
    setOperationError(null);
    void startVoiceScan(voiceIndex).then((nextJob) => {
      if (!mounted.current) return;
      scanBankByJob.current.set(nextJob.jobId, voiceIndex);
      setJob(nextJob);
      reconcileVoiceLabScanJob(nextJob, {
        onActive: (active) => setScanJobs((current) => ({ ...current, [active.jobId]: active })),
        onTerminal: completeScanJob,
      });
    }, (error) => {
      if (!mounted.current) return;
      clearRequestedBankScan(voiceIndex); setOperationError(error);
    });
  }, [clearRequestedBankScan, completeScanJob]);

  useEffect(() => {
    if (!visibleBanks.length) return;
    if (selection.bank === requestedSelection.bank && selection.line === requestedSelection.line) return;
    const next = new URLSearchParams(searchParams);
    if (selection.bank === null) next.delete("bank"); else next.set("bank", String(selection.bank));
    if (selection.line === null) next.delete("line"); else next.set("line", selection.line);
    setSearchParams(next, { replace: true });
  }, [visibleBanks, requestedSelection.bank, requestedSelection.line, searchParams, selection.bank, selection.line, setSearchParams]);

  useEffect(() => {
    if (selection.bank === null || indexedBanks.has(selection.bank)) return;
    ensureBankIndexed(selection.bank);
  }, [ensureBankIndexed, indexedBanks, selection.bank]);

  useEffect(() => {
    if (!activeScanJobs.length) return undefined;
    return pollVoiceLabJobs({
      jobs: activeScanJobs,
      getJob: getVoiceJob,
      onJob: (latest) => {
        setOperationError(null);
        setScanJobs((current) => ({ ...current, [latest.jobId]: latest }));
        setJob((current) => current?.jobId === latest.jobId ? latest : current);
      },
      onError: setOperationError,
      onComplete: completeScanJob,
    });
  }, [activeScanJobKey, completeScanJob]);

  useEffect(() => {
    if (!job || job.kind === "scan" || !isActiveVoiceLabJob(job)) return undefined;
    return pollVoiceLabJob({ job, getJob: getVoiceJob, onJob: (latest) => { setOperationError(null); setJob(latest); }, onError: setOperationError, onComplete: (latest) => {
      void (async () => {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["voice-lab-status"] }),
          queryClient.invalidateQueries({ queryKey: ["voice-lab-banks"] }),
          queryClient.invalidateQueries({ queryKey: ["voice-lab-findings"] }),
          queryClient.invalidateQueries({ queryKey: ["voice-line-detail"] }),
        ]);
        if (latest.kind === "analysis" && latest.status === "complete" && analysisBank.current !== null) {
          const refreshed = await queryClient.fetchQuery({ queryKey: ["voice-lab-findings"], queryFn: () => getVoiceFindings() });
          const target = findHighestRiskLine(refreshed, analysisBank.current);
          if (target) updateSelection(analysisBank.current, target);
        }
      })().catch(setOperationError);
    } });
  }, [job?.jobId, job?.status, queryClient]);

  async function refreshInventoryAfterMutation() {
    setRefreshError(null); setRefreshingInventory(true);
    try { await refreshVoiceLabAfterMutation({ startScan: () => startVoiceScan(selection.bank ?? undefined), getJob: getVoiceJob, onJob: setJob, invalidate: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["voice-lab-status"] }), queryClient.invalidateQueries({ queryKey: ["voice-lab-banks"] }), queryClient.invalidateQueries({ queryKey: ["voice-lab-findings"] }), queryClient.invalidateQueries({ queryKey: ["voice-lab-deployments"] })]); } }); }
    catch (error) { setRefreshError(error); } finally { setRefreshingInventory(false); }
  }
  const preflight = useMutation({ mutationFn: (recipeId: string) => preflightVoiceRecipe(recipeId), onSuccess: setDeployPlan });
  const deploy = useMutation({ mutationFn: (planId: string) => deployVoicePlan(planId), onSuccess: () => { setDeployPlan(null); void refreshInventoryAfterMutation(); } });
  const undo = useMutation({ mutationFn: (deploymentId: string) => undoVoiceDeployment(deploymentId), onSuccess: () => void refreshInventoryAfterMutation() });
  const analyze = useMutation({ mutationFn: (voiceIndex: number) => startVoiceAnalysis(voiceIndex), onSuccess: (nextJob, voiceIndex) => { analysisBank.current = voiceIndex; setOperationError(null); setJob(nextJob); }, onError: setOperationError });
  const cancel = useMutation({ mutationFn: (jobId: string) => cancelVoiceJob(jobId), onSuccess: (latest) => {
    setJob((current) => current?.jobId === latest.jobId ? latest : current);
    if (latest.kind === "scan") {
      reconcileVoiceLabScanJob(latest, {
        onActive: (active) => setScanJobs((current) => ({ ...current, [active.jobId]: active })),
        onTerminal: completeScanJob,
      });
    }
  }, onError: setOperationError });

  const currentStage = useMemo(() => {
    if (!selectedBank) return 0;
    if (job?.kind === "analysis" && isActiveVoiceLabJob(job)) return 1;
    if (!selectedLine) return bankFindings.length ? 2 : 1;
    return 3;
  }, [bankFindings.length, job, selectedBank, selectedLine]);
  const selectedName = selectedBank?.profiles[0]?.name ?? "this merc";
  const indexingName = visibleBanks.find((bank) => bank.voiceIndex === indexingBank)?.profiles[0]?.name ?? "selected merc";
  const editableLineCount = selectedBank?.lines.filter((line) => line.family === "speech" || line.family === "battle").length ?? 0;
  const selectedIndexing = selection.bank !== null && indexingBanks.has(selection.bank);
  const statusLabel = catalog.isLoading ? "Loading merc names" : indexingBank !== null ? `Indexing ${indexingName}’s voice files` : selectedIndexed ? `${selectedName} is ready` : "Merc names ready — choose one";

  return <main className="mx-auto max-w-[90rem] px-4 py-6 sm:px-6">
    <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3"><div><p className="font-mono text-[10px] uppercase tracking-[.22em] text-rust-400">Voice Lab</p><h1 className="text-2xl font-bold text-wasteland-100">Repair a merc’s voice, one line at a time</h1><p className="mt-1 text-sm text-wasteland-400">Nothing changes in the game until you review and approve a deployment.</p></div><Link className="btn-ghost text-xs" to="/hub">Back to Merc Forge</Link></div>
    <ol aria-label="Voice Lab workflow" className="mb-4 grid grid-cols-2 border border-wasteland-700 bg-wasteland-950/45 sm:grid-cols-3 lg:grid-cols-6">{stages.map((stage, index) => <li key={stage} className={`border-wasteland-700 px-3 py-2 text-[10px] uppercase tracking-wide sm:border-r ${index <= currentStage ? "text-rust-300" : "text-wasteland-600"}`}><span className="mr-1 font-mono">{index + 1}</span> {stage}</li>)}</ol>
    <section aria-label="Voice Lab status" className="mb-4 border border-wasteland-700 bg-wasteland-900/50 px-3 py-2"><div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-wasteland-300"><span className="font-mono text-rust-400">{statusLabel}</span>{displayJob && <span>{displayJob.kind === "scan" && isActiveVoiceLabJob(displayJob) ? `${displayJob.completed.toLocaleString()} selected files indexed` : `${displayJob.lastMessage}${displayJob.total > 0 ? ` (${displayJob.completed}/${displayJob.total})` : ""}`}</span>}{displayJob && isActiveVoiceLabJob(displayJob) && <button type="button" title="Stop this background job without publishing partial audit results" className="btn-ghost px-2 py-1 text-[10px]" disabled={cancel.isPending || displayJob.status === "cancelling"} onClick={() => cancel.mutate(displayJob.jobId)}>Cancel</button>}{refreshingInventory && <span>Refreshing the selected merc after the change…</span>}</div>{status.data?.transcriberConfigured === false && <p className="mt-1 text-xs text-amber-300">Transcription is not configured. Open Settings → Voice Lab before analyzing lines.</p>}{status.data?.deployment.toolchainAvailable === false && <p className="mt-1 text-xs text-amber-300">FFmpeg is not ready. Open Settings → Voice Lab to enable waveform, preview, and deployment.</p>}{(status.isError || catalog.isError || Boolean(operationError)) && <p className="mt-1 text-xs text-rust-400">{formatApiError(status.error ?? catalog.error ?? operationError)}</p>}</section>
    <div className="grid gap-4 xl:grid-cols-[17rem_minmax(0,1fr)]"><BankBrowser banks={visibleBanks} selectedBank={selection.bank} indexedBanks={indexedBanks} indexingBank={indexingBank} onSelect={(voiceIndex) => { updateSelection(voiceIndex); if (!indexedBanks.has(voiceIndex)) ensureBankIndexed(voiceIndex); }} />
      <section className="min-w-0">{!selectedBank ? <div className="border border-dashed border-wasteland-700 px-6 py-16 text-center"><h2 className="text-lg font-semibold text-wasteland-200">Choose a merc to begin</h2><p className="mt-2 text-sm text-wasteland-400">Names load first. The selected merc’s voice files are indexed only when you open them.</p></div> : !selectedIndexed ? <div className="border border-dashed border-wasteland-700 px-6 py-16 text-center"><h2 className="text-lg font-semibold text-wasteland-200">{selectedIndexing ? `Indexing ${selectedName}’s voice files…` : `${selectedName} is not indexed yet`}</h2><p className="mt-2 text-sm text-wasteland-400">Only this merc’s recordings and subtitles are loaded.</p>{!selectedIndexing && <button type="button" className="btn-secondary mt-4 text-xs" onClick={() => ensureBankIndexed(selectedBank.voiceIndex)}>Index {selectedName}</button>}</div> : <>
        <header className="mb-4 border border-wasteland-700 bg-wasteland-900/45 p-4"><div className="flex flex-wrap items-center justify-between gap-4"><div><p className="text-[10px] font-semibold uppercase tracking-[.18em] text-rust-400">2. Analyze lines</p><h2 className="mt-1 text-xl font-semibold text-wasteland-100">{selectedName}</h2><p className="mt-1 text-xs text-wasteland-400">Transcribes only recordings not already cached, then checks all {editableLineCount} lines for repeated audio, subtitle mismatches, missing files, and timing problems.</p></div><button type="button" title="Transcribe missing recordings, then check this merc's complete voice set for likely problems" className="btn-primary px-4 py-2 text-xs" disabled={analyze.isPending || Boolean(displayJob && isActiveVoiceLabJob(displayJob)) || status.data?.transcriberConfigured === false} onClick={() => analyze.mutate(selectedBank.voiceIndex)}>{job?.kind === "analysis" && isActiveVoiceLabJob(job) && analysisBank.current === selectedBank.voiceIndex ? "Analyzing…" : `Analyze ${selectedName}’s lines`}</button></div></header>
        <div className="grid items-start gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]"><LineBrowser lines={selectedBank.lines} findings={bankFindings} selectedLine={selection.line} onSelect={(line) => updateSelection(selectedBank.voiceIndex, voiceLineKey(line.family, line.lineId))} />
          {selectedLine ? <LineWorkbench installId={status.data?.installId ?? "active"} bank={selectedBank} line={selectedLine} findings={findings.data ?? []} openingDeployReview={preflight.isPending} onDeployRecipe={async (recipeId) => { try { await preflight.mutateAsync(recipeId); } catch (error) { setOperationError(error); } }} /> : <div className="border border-dashed border-wasteland-700 px-6 py-14"><h3 className="text-base font-semibold text-wasteland-200">Choose any line from the list</h3><p className="mt-2 text-sm text-wasteland-400">After analysis, the most serious unresolved issue opens automatically. You can always inspect or edit a different line.</p>{bankFindings.length === 0 && <p className="mt-3 text-xs text-amber-300">No saved issues are shown yet. Analyze this merc to create transcripts and check the recordings.</p>}</div>}
        </div>
        <details className="mt-4 border border-wasteland-700 bg-wasteland-900/30"><summary className="cursor-pointer px-4 py-3 text-sm font-medium text-wasteland-200">Issues found for {selectedName} ({bankFindings.length})</summary><div className="border-t border-wasteland-700 p-3">{findings.isLoading ? <p className="text-sm text-wasteland-400">Loading saved issues…</p> : <RiskQueue findings={bankFindings} banks={banks.data ?? []} portraitSheet={portraits.data} selectedLine={selection.line} onOpen={(finding) => { if (finding.family && finding.lineId) updateSelection(selectedBank.voiceIndex, voiceLineKey(finding.family, finding.lineId)); }} />}</div></details>
      </>}</section></div>
    {deployPlan && <DeployDialog plan={deployPlan} profiles={visibleBanks.flatMap((bank) => bank.profiles)} deploying={deploy.isPending} onClose={() => setDeployPlan(null)} onDeploy={async (planId) => deploy.mutateAsync(planId).then(() => undefined)} />}
    {preflight.isError && <p className="mt-4 text-sm text-rust-300">{formatApiError(preflight.error)}</p>}{history.isError && <p className="mt-4 text-sm text-rust-300">{formatApiError(history.error)}</p>}{Boolean(refreshError) && <p className="mt-4 text-sm text-rust-300">The change completed, but the refreshed voice inventory could not be loaded: {formatApiError(refreshError)} Refresh before another change.</p>}
    <HistoryPanel deployments={history.data ?? []} recoveryRequired={deployPlan?.recoveryRequired ?? status.data?.deployment.recoveryRequired ?? null} undoingId={undo.isPending ? undo.variables : null} onUndo={(deploymentId) => undo.mutateAsync(deploymentId)} onOpenRecovery={() => navigate("/backups")} />
  </main>;
}
