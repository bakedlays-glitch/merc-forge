// frontend/src/components/forms/ItemClassStatsForm.tsx
import type { ItemFieldSpec } from "../../lib/api";
import EnumSelect from "../items/EnumSelect";
import CollapsibleSection from "../items/CollapsibleSection";
import FieldHelp from "../items/FieldHelp";

interface Props {
  family: string;
  schema: ItemFieldSpec[];
  fields: Record<string, number>;
  enumOptions: Record<string, { value: number; label: string }[]>;
  onChange: (key: string, val: number) => void;
}

export default function ItemClassStatsForm({ family, schema, fields, enumOptions, onChange }: Props) {
  if (!schema.length) return null;
  return (
    <CollapsibleSection title={`${family} stats`} defaultOpen>
      <div className="grid grid-cols-2 gap-2">
        {schema.map((f) => {
          const labelText = `${f.label}${f.unit ? ` (${f.unit})` : ""}`;
          if (enumOptions[f.key]) {
            return (
              <EnumSelect key={f.key} label={labelText} value={fields[f.key] ?? 0}
                options={enumOptions[f.key]!} onChange={(v) => onChange(f.key, v)} help={f.help} />
            );
          }
          return (
            <label key={f.key} className="flex flex-col text-xs gap-0.5">
              <span className="flex items-center gap-1 text-wasteland-300">
                {labelText}
                {f.help && <FieldHelp help={f.help} />}
              </span>
              <input className="input" type="number" min={f.min} max={f.max}
                value={fields[f.key] ?? 0}
                onChange={(e) => onChange(f.key, Number(e.target.value))} />
            </label>
          );
        })}
      </div>
    </CollapsibleSection>
  );
}
