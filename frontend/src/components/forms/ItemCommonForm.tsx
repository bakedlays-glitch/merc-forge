// frontend/src/components/forms/ItemCommonForm.tsx
import { useState } from "react";
import type { ItemFieldSpec } from "../../lib/api";
import BigItemPicker from "../BigItemPicker";

interface Props {
  schema: ItemFieldSpec[];
  strings: Record<string, string>;
  ints: Record<string, number>;
  onStr: (key: string, val: string) => void;
  onInt: (key: string, val: number) => void;
  onPickGraphic: (g: { type: number; num: number }) => void;
}

export default function ItemCommonForm(props: Props) {
  const { schema, strings, ints, onStr, onInt, onPickGraphic } = props;
  const [picking, setPicking] = useState(false);
  const groups = [...new Set(schema.map((f) => f.group))];
  const gType = ints["ubGraphicType"] ?? 0;
  const gNum = ints["ubGraphicNum"] ?? 0;
  return (
    <div className="space-y-4">
      {groups.map((group) => (
        <fieldset key={group} className="border border-wasteland-700 rounded p-2">
          <legend className="text-xs text-wasteland-400 px-1">{group}</legend>
          <div className="grid grid-cols-2 gap-2">
            {schema.filter((f) => f.group === group).map((f) => (
              <label key={f.key} className="flex flex-col text-xs gap-0.5"
                     title={f.note ?? f.key}>
                <span className={f.advanced ? "text-wasteland-500" : "text-wasteland-300"}>
                  {f.label}{f.advanced ? " (advanced)" : ""}
                </span>
                {f.kind === "str" ? (
                  f.key === "szItemDesc" || f.key === "szBRDesc" ? (
                    <textarea className="input" rows={2} maxLength={f.cap}
                      value={strings[f.key] ?? ""}
                      onChange={(e) => onStr(f.key, e.target.value)} />
                  ) : (
                    <input className="input" maxLength={f.cap}
                      value={strings[f.key] ?? ""}
                      onChange={(e) => onStr(f.key, e.target.value)} />
                  )
                ) : (
                  <input className="input" type="number" min={f.min} max={f.max}
                    value={ints[f.key] ?? 0}
                    onChange={(e) => onInt(f.key, Number(e.target.value))} />
                )}
              </label>
            ))}
          </div>
          {group === "Graphic" && (
            <button className="btn-secondary text-xs mt-2"
                    onClick={() => setPicking(true)}>
              Change graphic…
            </button>
          )}
        </fieldset>
      ))}
      {picking && (
        <BigItemPicker
          value={{ type: gType, num: gNum }}
          onPick={(g) => { onPickGraphic(g); setPicking(false); }}
          onClose={() => setPicking(false)}
        />
      )}
    </div>
  );
}
