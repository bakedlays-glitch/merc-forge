// frontend/src/components/items/EnumSelect.tsx

export interface EnumOption {
  value: number;
  label: string;
}

interface Props {
  label: string;
  value: number;
  options: EnumOption[];
  onChange: (val: number) => void;
  help?: string;
}

/**
 * A labelled <select> for coded enum fields.
 * If `value` is not present in `options`, prepends an "Unknown (N)" option
 * so out-of-range data still renders and can be replaced.
 */
export default function EnumSelect({ label, value, options, onChange, help }: Props) {
  const inOptions = options.some((o) => o.value === value);
  const displayOptions: EnumOption[] = inOptions
    ? options
    : [{ value, label: `Unknown (${value})` }, ...options];

  return (
    <label className="flex flex-col text-xs gap-0.5" title={help ?? label}>
      <span className="text-wasteland-300">{label}</span>
      <select
        className="input"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        {displayOptions.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
