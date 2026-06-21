// frontend/src/components/forms/ItemClassStatsForm.tsx
import type { ItemFieldSpec } from "../../lib/api";

interface Props {
  family: string;
  schema: ItemFieldSpec[];
  fields: Record<string, number>;
  onChange: (key: string, val: number) => void;
}

export default function ItemClassStatsForm({ family, schema, fields, onChange }: Props) {
  if (!schema.length) return null;
  return (
    <fieldset className="border border-wasteland-700 rounded p-2">
      <legend className="text-xs text-rust-400 px-1">{family} stats</legend>
      <div className="grid grid-cols-2 gap-2">
        {schema.map((f) => (
          <label key={f.key} className="flex flex-col text-xs gap-0.5" title={f.key}>
            <span className="text-wasteland-300">{f.label}</span>
            <input className="input" type="number" min={f.min} max={f.max}
              value={fields[f.key] ?? 0}
              onChange={(e) => onChange(f.key, Number(e.target.value))} />
          </label>
        ))}
      </div>
    </fieldset>
  );
}
