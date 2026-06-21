import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listItems, getItem, updateItem, itemGraphicUrl,
  type ItemSummary, type ItemDetail,
} from "../lib/api";
import ItemCommonForm from "../components/forms/ItemCommonForm";
import ItemClassStatsForm from "../components/forms/ItemClassStatsForm";

function Thumb({ id }: { id: number }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let alive = true;
    itemGraphicUrl(id).then((u) => { if (alive) setSrc(u); });
    return () => { alive = false; };
  }, [id]);
  return src
    ? <img src={src} alt="" className="h-6 w-6 object-contain"
           onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")} />
    : <div className="h-6 w-6" />;
}

export default function Items() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["items"], queryFn: () => listItems() });
  const [selected, setSelected] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [draft, setDraft] = useState<ItemDetail | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string>("");

  const detail = useQuery({
    queryKey: ["item", selected],
    queryFn: () => getItem(selected as number),
    enabled: selected !== null,
  });
  useEffect(() => { if (detail.data) setDraft(detail.data); }, [detail.data]);

  const rows = useMemo(() => {
    const items = list.data?.items ?? [];
    const needle = q.toLowerCase();
    return items.filter(
      (it) => it.name.toLowerCase().includes(needle) || String(it.ui_index) === q,
    );
  }, [list.data, q]);

  async function save() {
    if (!draft || selected === null) return;
    setSaving(true);
    setMsg("");
    try {
      const res = await updateItem(selected, {
        strings: draft.strings,
        ints: draft.ints,
        class_fields: draft.class_fields ?? {},
      });
      const clampNote = res.clamps?.length
        ? ` (${res.clamps.length} value(s) clamped)` : "";
      setMsg(`Saved${clampNote}. Backup ${res.backup_id ?? ""}.`);
      await qc.invalidateQueries({ queryKey: ["items"] });
      await qc.invalidateQueries({ queryKey: ["item", selected] });
    } catch (e) {
      setMsg(`Save failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full gap-3 p-3">
      <div className="w-80 flex flex-col">
        <h1 className="font-bold text-wasteland-100 mb-2">Items</h1>
        <input className="input mb-2" placeholder="Search name or id…"
               value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="flex-1 overflow-y-auto border border-wasteland-700 rounded">
          {rows.slice(0, 400).map((it: ItemSummary) => (
            <button key={it.ui_index}
              onClick={() => setSelected(it.ui_index)}
              className={`flex items-center gap-2 w-full px-2 py-1 text-left text-xs ${
                selected === it.ui_index ? "bg-wasteland-800" : ""}`}>
              <Thumb id={it.ui_index} />
              <span className="text-wasteland-500 w-10">{it.ui_index}</span>
              <span className="flex-1 truncate text-wasteland-200">{it.name}</span>
              <span className="text-wasteland-500">${it.price}</span>
            </button>
          ))}
          {rows.length > 400 && (
            <p className="text-[11px] text-wasteland-500 p-2">
              Showing first 400 of {rows.length}. Refine your search.
            </p>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {selected === null && <p className="text-wasteland-500 text-sm">Select an item to edit.</p>}
        {selected !== null && draft && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-bold text-wasteland-100">
                #{draft.ui_index} {draft.strings["szItemName"] ?? ""}
              </h2>
              <button className="btn-primary text-xs" disabled={saving} onClick={save}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
            {msg && <p className="text-xs text-rust-300">{msg}</p>}
            <ItemCommonForm
              schema={list.data?.common_schema ?? []}
              strings={draft.strings}
              ints={draft.ints}
              onStr={(k, v) => setDraft({ ...draft, strings: { ...draft.strings, [k]: v } })}
              onInt={(k, v) => setDraft({ ...draft, ints: { ...draft.ints, [k]: v } })}
              onPickGraphic={(g) => setDraft({
                ...draft,
                ints: { ...draft.ints, ubGraphicType: g.type, ubGraphicNum: g.num },
              })}
            />
            {draft.family && draft.class_schema && (
              <ItemClassStatsForm
                family={draft.family}
                schema={draft.class_schema}
                fields={draft.class_fields ?? {}}
                onChange={(k, v) => setDraft({
                  ...draft,
                  class_fields: { ...(draft.class_fields ?? {}), [k]: v },
                })}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
