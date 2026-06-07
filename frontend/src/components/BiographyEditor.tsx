interface Props {
  label: string;
  value: string;
  onChange: (v: string) => void;
  maxLength: number;
  /** Warning threshold relative to maxLength (default: 0.95) */
  warnThreshold?: number;
  rows?: number;
}

export default function BiographyEditor({
  label,
  value,
  onChange,
  maxLength,
  warnThreshold = 0.95,
  rows = 6,
}: Props) {
  // Coerce undefined/null to empty string. Mercs loaded via "Start
  // from existing" off an NPC/RPC or a modded slot lacking
  // <biographyText>/<additionalInfoText> XML nodes arrive here with
  // value=undefined, and `undefined.length` throws an uncaught
  // TypeError that crashes the React tree. Bug #6 in
  // MERC_FORGE_BUG_LIST.md.
  const safeValue = value ?? "";
  const len = safeValue.length;
  const overLimit = len > maxLength;
  const nearLimit = !overLimit && len >= maxLength * warnThreshold;

  const counterColor = overLimit
    ? "text-rust-500"
    : nearLimit
    ? "text-yellow-400"
    : "text-wasteland-400";

  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label className="text-sm font-medium text-wasteland-100">{label}</label>
        <span className={`text-xs font-mono ${counterColor}`}>
          {len} / {maxLength}
        </span>
      </div>
      <textarea
        rows={rows}
        value={safeValue}
        onChange={(e) => onChange(e.target.value)}
        className={`input font-sans resize-y ${overLimit ? "border-rust-500" : ""}`}
      />
      {overLimit && (
        <div className="text-xs text-rust-400 mt-1">
          Over the {maxLength}-character limit. You'll need to trim it before you can save this merc.
        </div>
      )}
    </div>
  );
}
