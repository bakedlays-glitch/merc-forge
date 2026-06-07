import type { AuditIssue } from "../lib/schema";

interface Props {
  issues: AuditIssue[];
  className?: string;
}

const ICON = {
  info: "ℹ",
  warn: "⚠",
  error: "✗",
} as const;

const CLASS = {
  info: "text-blue-400 border-blue-500/30 bg-blue-500/10",
  warn: "text-yellow-400 border-yellow-500/30 bg-yellow-500/10",
  error: "text-rust-400 border-rust-500/30 bg-rust-500/10",
} as const;

export default function AuditPanel({ issues, className = "" }: Props) {
  if (issues.length === 0) {
    return (
      <div className={`text-sm text-wasteland-400 ${className}`}>
        ✓ No issues detected — ready to compile.
      </div>
    );
  }
  return (
    <ul className={`space-y-2 ${className}`}>
      {issues.map((issue, idx) => (
        <li
          key={idx}
          className={`rounded border px-3 py-2 text-sm ${CLASS[issue.severity]}`}
        >
          <div className="flex items-start gap-2">
            <span className="font-mono">{ICON[issue.severity]}</span>
            <div className="flex-1 min-w-0">
              <div className="font-medium">
                {issue.message}
                {issue.field && (
                  <code className="ml-2 text-xs opacity-70 font-mono">
                    {issue.field}
                  </code>
                )}
              </div>
              {issue.suggested_fix && (
                <div className="mt-1 text-xs opacity-80">
                  Suggested fix: {issue.suggested_fix}
                </div>
              )}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
