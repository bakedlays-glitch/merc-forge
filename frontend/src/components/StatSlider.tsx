interface Props {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  hint?: string;
}

export default function StatSlider({
  label,
  value,
  onChange,
  min = 0,
  max = 100,
  hint,
}: Props) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-sm font-medium text-wasteland-100">{label}</span>
        <span className="font-mono text-sm text-rust-400 tabular-nums">{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-rust-500"
      />
      {hint && <div className="text-xs text-wasteland-400 mt-0.5">{hint}</div>}
    </label>
  );
}
