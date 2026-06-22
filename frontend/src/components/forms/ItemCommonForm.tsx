// frontend/src/components/forms/ItemCommonForm.tsx
import { useEffect, useState } from "react";
import { bigItemGraphicUrl, type ItemFieldSpec } from "../../lib/api";
import BigItemPicker from "../BigItemPicker";
import EnumSelect from "../items/EnumSelect";
import ClassBadge from "../items/ClassBadge";
import CollapsibleSection from "../items/CollapsibleSection";
import FieldHelp from "../items/FieldHelp";

interface Props {
  schema: ItemFieldSpec[];
  strings: Record<string, string>;
  ints: Record<string, number>;
  enumOptions: Record<string, { value: number; label: string }[]>;
  classLabel: string;
  onStr: (key: string, val: string) => void;
  onInt: (key: string, val: number) => void;
  onPickGraphic: (g: { type: number; num: number }) => void;
}

// Render order; "Advanced" collapses by default. Any group not listed here
// (shouldn't happen) falls to the end, open.
const SECTION_ORDER = ["Identity", "Economy", "Graphic", "Advanced"];

function GraphicPreview({ type, num }: { type: number; num: number }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let alive = true;
    bigItemGraphicUrl(type, num).then((u) => { if (alive) setSrc(u); }).catch(() => {});
    return () => { alive = false; };
  }, [type, num]);
  return (
    <div className="flex items-center gap-2">
      <div className="border border-wasteland-700 rounded p-1 bg-wasteland-900">
        {src
          ? <img src={src} alt="current graphic" className="h-10 object-contain"
                 style={{ imageRendering: "pixelated" }} />
          : <div className="h-10 w-10" />}
      </div>
      <span className="text-[11px] text-wasteland-500">type {type}, num {num}</span>
    </div>
  );
}

export default function ItemCommonForm(props: Props) {
  const { schema, strings, ints, enumOptions, classLabel, onStr, onInt, onPickGraphic } = props;
  const [picking, setPicking] = useState(false);
  const gType = ints["ubGraphicType"] ?? 0;
  const gNum = ints["ubGraphicNum"] ?? 0;

  const groupsPresent = [...new Set(schema.map((f) => f.group))];
  const ordered = [
    ...SECTION_ORDER.filter((g) => groupsPresent.includes(g)),
    ...groupsPresent.filter((g) => !SECTION_ORDER.includes(g)),
  ];

  function renderField(f: ItemFieldSpec) {
    const labelText = `${f.label}${f.unit ? ` (${f.unit})` : ""}`;
    const labelNode = (
      <span className="flex items-center gap-1 text-wasteland-300">
        {labelText}
        {f.help && <FieldHelp help={f.help} />}
      </span>
    );
    if (f.kind === "str") {
      const isLong = f.key === "szItemDesc" || f.key === "szBRDesc";
      return (
        <label key={f.key} className="flex flex-col text-xs gap-0.5">
          {labelNode}
          {isLong
            ? <textarea className="input" rows={2} maxLength={f.cap}
                value={strings[f.key] ?? ""} onChange={(e) => onStr(f.key, e.target.value)} />
            : <input className="input" maxLength={f.cap}
                value={strings[f.key] ?? ""} onChange={(e) => onStr(f.key, e.target.value)} />}
        </label>
      );
    }
    if (enumOptions[f.key]) {
      return (
        <EnumSelect key={f.key} label={labelText} value={ints[f.key] ?? 0}
          options={enumOptions[f.key]!} onChange={(v) => onInt(f.key, v)} help={f.help} />
      );
    }
    return (
      <label key={f.key} className="flex flex-col text-xs gap-0.5">
        {labelNode}
        <input className="input" type="number" min={f.min} max={f.max}
          value={ints[f.key] ?? 0} onChange={(e) => onInt(f.key, Number(e.target.value))} />
      </label>
    );
  }

  return (
    <div className="space-y-3">
      {/* Read-only class badge — usItemClass is engine-derived, never editable. */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-wasteland-400">Item Class</span>
        <ClassBadge classLabel={classLabel} />
      </div>

      {ordered.map((group) => {
        // usItemClass is shown as the badge above — exclude it from inputs.
        const fields = schema.filter((f) => f.group === group && f.key !== "usItemClass");
        if (!fields.length && group !== "Graphic") return null;
        return (
          <CollapsibleSection key={group} title={group} defaultOpen={group !== "Advanced"}>
            <div className="grid grid-cols-2 gap-2">
              {fields.map(renderField)}
            </div>
            {group === "Graphic" && (
              <div className="mt-2 space-y-2">
                <GraphicPreview type={gType} num={gNum} />
                <button className="btn-secondary text-xs" onClick={() => setPicking(true)}>
                  Change graphic…
                </button>
              </div>
            )}
          </CollapsibleSection>
        );
      })}

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
