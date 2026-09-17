import { useQuery } from "@tanstack/react-query";

import { formatApiError, getRpcReadiness } from "../lib/api";
import { readinessIssueCount, readinessSteps } from "../lib/rpcWorkflow";

const STATUS_STYLE = {
  ready: "border-emerald-500/50 bg-emerald-950/30 text-emerald-200",
  blocked: "border-rust-500/60 bg-rust-950/35 text-rust-100",
  advisory: "border-amber-500/40 bg-amber-950/20 text-amber-200",
} as const;

export default function RpcReadinessRail({
  profile,
  onNavigate,
}: {
  profile: number;
  onNavigate: (tab: "profile" | "portrait" | "voice" | "placement" | "recruitment") => void;
}) {
  const query = useQuery({
    queryKey: ["rpc-readiness", profile],
    queryFn: () => getRpcReadiness(profile),
  });

  if (query.isLoading) {
    return <div className="card text-xs text-wasteland-500">Checking RPC field kit…</div>;
  }
  if (query.isError || !query.data) {
    return <div className="card text-xs text-rust-300">Readiness check failed: {formatApiError(query.error)}</div>;
  }

  const steps = readinessSteps(query.data);
  const issueCount = readinessIssueCount(steps);
  return (
    <section className="overflow-hidden rounded border border-wasteland-600 bg-wasteland-950/55">
      <div className="flex items-center justify-between gap-4 border-b border-wasteland-700 px-4 py-3">
        <div>
          <div className="font-mono text-[11px] uppercase tracking-[.12em] text-wasteland-400">RPC checklist · slot {profile}</div>
          <div className="mt-0.5 text-base font-semibold text-wasteland-50">
            {query.data.ready
              ? "Ready for a new-game test"
              : `${issueCount} item${issueCount === 1 ? "" : "s"} need attention`}
          </div>
        </div>
        <span className={`shrink-0 rounded-sm border px-2.5 py-1 font-mono text-[11px] uppercase ${
          query.data.ready ? STATUS_STYLE.ready : STATUS_STYLE.blocked
        }`}>
          {query.data.ready ? "Ready" : `${issueCount} issue${issueCount === 1 ? "" : "s"}`}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-px bg-wasteland-800 sm:grid-cols-2 lg:grid-cols-3">
        {steps.map((step) => (
          <button
            key={step.key}
            type="button"
            className="min-h-24 border-0 bg-wasteland-950 p-4 text-left transition-colors hover:bg-wasteland-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-rust-400"
            onClick={() => onNavigate(step.tab)}
            title={step.detail}
          >
            <span className="flex items-center gap-2">
              <span className={`inline-block h-2.5 w-2.5 rounded-full border ${STATUS_STYLE[step.status]}`} />
              <span className="text-sm font-semibold text-wasteland-50">{step.label}</span>
            </span>
            <span className="mt-2 block line-clamp-3 text-xs leading-5 text-wasteland-300">{step.detail}</span>
          </button>
        ))}
      </div>
      <p className="border-t border-wasteland-800 px-4 py-2 text-xs text-wasteland-400">
        Map placement appears in a new campaign. Existing saves keep their current map state.
      </p>
    </section>
  );
}
