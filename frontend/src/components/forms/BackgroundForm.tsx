import { useMemo } from "react";

import type { BackgroundFieldSpec } from "../../lib/api";

/**
 * Schema-driven editor for a single background's name/short/description plus
 * every engine modifier field (int / flag / enum), grouped into collapsible
 * sections. The field list + clamp ranges come from the sidecar
 * (backgrounds_schema.py) so this form never hard-codes the JA2 field set.
 *
 * String caps are measured in UTF-16 code units — which is exactly what
 * JS `string.length` returns — matching the engine's CHAR16[] truncation.
 */

export interface BackgroundDraft {
  name: string;
  short_name: string;
  description: string;
  fields: Record<string, number>;
}

interface Props {
  schema: BackgroundFieldSpec[];
  draft: BackgroundDraft;
  onChange: (patch: Partial<BackgroundDraft>) => void;
  caps: { name: number; short: number; description: number };
}

function CharCount({ value, max }: { value: string; max: number }) {
  // JS string.length is UTF-16 code units — the same unit the engine caps on.
  const over = value.length > max;
  return (
    <span className={`text-[10px] ${over ? "text-rust-400" : "text-wasteland-500"}`}>
      {value.length}/{max}
    </span>
  );
}

export default function BackgroundForm({ schema, draft, onChange, caps }: Props) {
  const groups = useMemo(() => {
    const out: { name: string; fields: BackgroundFieldSpec[] }[] = [];
    for (const f of schema) {
      let g = out.find((x) => x.name === f.group);
      if (!g) {
        g = { name: f.group, fields: [] };
        out.push(g);
      }
      g.fields.push(f);
    }
    return out;
  }, [schema]);

  const setField = (key: string, v: number) =>
    onChange({ fields: { ...draft.fields, [key]: v } });

  return (
    <div className="space-y-4">
      {/* Identity */}
      <fieldset className="border border-wasteland-700 rounded p-3">
        <legend className="text-sm font-medium text-wasteland-100 px-1">Identity</legend>
        <label className="block mt-1">
          <span className="flex items-center justify-between">
            <span className="text-xs text-wasteland-300">Name</span>
            <CharCount value={draft.name} max={caps.name} />
          </span>
          <input
            className="input mt-1"
            value={draft.name}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder="e.g. Desert Ranger"
          />
        </label>
        <label className="block mt-2">
          <span className="flex items-center justify-between">
            <span className="text-xs text-wasteland-300">Short name (laptop display)</span>
            <CharCount value={draft.short_name} max={caps.short} />
          </span>
          <input
            className="input mt-1"
            value={draft.short_name}
            onChange={(e) => onChange({ short_name: e.target.value })}
          />
        </label>
        <label className="block mt-2">
          <span className="flex items-center justify-between">
            <span className="text-xs text-wasteland-300">Description</span>
            <CharCount value={draft.description} max={caps.description} />
          </span>
          <textarea
            className="input mt-1 h-24 resize-y font-mono text-xs"
            value={draft.description}
            onChange={(e) => onChange({ description: e.target.value })}
          />
        </label>
      </fieldset>

      {/* Modifier groups */}
      {groups.map((g) => {
        const nonZero = g.fields.filter((f) => (draft.fields[f.key] ?? 0) !== 0).length;
        return (
          <details
            key={g.name}
            open={nonZero > 0}
            className="border border-wasteland-700 rounded p-3 open:bg-wasteland-900/40"
          >
            <summary className="cursor-pointer text-sm font-medium text-wasteland-100 select-none">
              {g.name}
              <span className="ml-2 text-xs text-wasteland-500">
                {nonZero === 0 ? "(none set)" : `(${nonZero} set)`}
              </span>
            </summary>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-3">
              {g.fields.map((f) => {
                const v = draft.fields[f.key] ?? 0;
                return (
                  <label key={f.key} className="block" title={f.note ?? undefined}>
                    <span className="text-xs text-wasteland-300 flex items-center gap-1">
                      {f.label}
                      {f.note && <span className="text-rust-400">*</span>}
                    </span>
                    {f.kind === "flag" ? (
                      <input
                        type="checkbox"
                        className="mt-1.5 accent-rust-500 h-4 w-4"
                        checked={v !== 0}
                        onChange={(e) => setField(f.key, e.target.checked ? 1 : 0)}
                      />
                    ) : f.kind === "enum" && f.options ? (
                      <select
                        className="input mt-1"
                        value={v}
                        onChange={(e) => setField(f.key, Number(e.target.value))}
                      >
                        {f.options.map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="number"
                        className="input mt-1 font-mono text-xs"
                        min={f.min}
                        max={f.max}
                        value={v}
                        onChange={(e) => {
                          const n = e.target.value === "" || e.target.value === "-"
                            ? 0
                            : Number(e.target.value);
                          setField(f.key, Number.isFinite(n) ? n : 0);
                        }}
                      />
                    )}
                    {f.kind === "int" && (
                      <span className="text-[10px] text-wasteland-600">
                        {f.min}..{f.max}
                      </span>
                    )}
                  </label>
                );
              })}
            </div>
            {g.fields.some((f) => f.note) && (
              <p className="text-[10px] text-wasteland-400 mt-2">
                {g.fields.filter((f) => f.note).map((f) => (
                  <span key={f.key} className="block">
                    <span className="text-rust-400">*</span> {f.label}: {f.note}
                  </span>
                ))}
              </p>
            )}
          </details>
        );
      })}
      <p className="text-xs text-wasteland-500">
        Values outside the engine's range are clamped on save (you'll see what changed).
        Backgrounds.xml is shared by every merc — a backup is taken automatically
        before each write.
      </p>
    </div>
  );
}
