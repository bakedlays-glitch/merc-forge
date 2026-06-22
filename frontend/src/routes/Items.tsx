import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listItems, getItem, updateItem, itemGraphicUrl,
  type ItemSummary, type ItemDetail,
} from "../lib/api";
import ItemCommonForm from "../components/forms/ItemCommonForm";
import ItemClassStatsForm from "../components/forms/ItemClassStatsForm";
import CategoryTabs from "../components/items/CategoryTabs";
import ConfirmModal from "../components/ConfirmModal";

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

type SortKey = "id" | "name" | "price" | "coolness";

// Compare only the editable surface of a detail record.
function editableEq(a: ItemDetail | null, b: ItemDetail | null): boolean {
  if (!a || !b) return a === b;
  const pick = (d: ItemDetail) =>
    JSON.stringify({ s: d.strings, i: d.ints, c: d.class_fields ?? {} });
  return pick(a) === pick(b);
}

export default function Items() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["items"], queryFn: () => listItems() });
  const [selected, setSelected] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [activeCategory, setActiveCategory] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("id");
  const [draft, setDraft] = useState<ItemDetail | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string>("");
  // Pending navigation held while a dirty-guard confirm is open.
  const [pendingNav, setPendingNav] = useState<{ kind: "select"; id: number } | { kind: "category"; key: string } | null>(null);

  const detail = useQuery({
    queryKey: ["item", selected],
    queryFn: () => getItem(selected as number),
    enabled: selected !== null,
  });
  useEffect(() => { if (detail.data) setDraft(detail.data); }, [detail.data]);

  const writable = list.data?.writable ?? true;
  const isDirty = !editableEq(draft, detail.data ?? null);

  const allItems = list.data?.items ?? [];
  const categories = list.data?.categories ?? [];

  const rows = useMemo(() => {
    const needle = q.toLowerCase();
    const filtered = allItems.filter(
      (it) =>
        (activeCategory === "all" || it.category === activeCategory) &&
        (it.name.toLowerCase().includes(needle) || String(it.ui_index) === q),
    );
    const sorted = [...filtered];
    sorted.sort((a, b) => {
      switch (sortKey) {
        case "name": return a.name.localeCompare(b.name);
        case "price": return b.price - a.price;
        case "coolness": return b.coolness - a.coolness;
        default: return a.ui_index - b.ui_index;
      }
    });
    return sorted;
  }, [allItems, q, activeCategory, sortKey]);

  // ── Navigation with unsaved-edit guard ──────────────────────────────────
  function requestSelect(id: number) {
    if (id === selected) return;
    if (isDirty) { setPendingNav({ kind: "select", id }); return; }
    setSelected(id);
  }
  function requestCategory(key: string) {
    if (key === activeCategory) return;
    if (isDirty) { setPendingNav({ kind: "category", key }); return; }
    setActiveCategory(key);
    setSelected(null);
  }
  function applyPending() {
    if (!pendingNav) return;
    if (pendingNav.kind === "select") setSelected(pendingNav.id);
    else { setActiveCategory(pendingNav.key); setSelected(null); }
    setPendingNav(null);
  }

  async function save() {
    if (!draft || selected === null || !isDirty || !writable) return;
    setSaving(true);
    setMsg("");
    try {
      const res = await updateItem(selected, {
        strings: draft.strings,
        ints: draft.ints,
        class_fields: draft.class_fields ?? {},
      });
      // Snap the form to the server's post-clamp values so it reflects disk.
      if (res.clamps?.length) {
        const ints = { ...draft.ints };
        const cf = { ...(draft.class_fields ?? {}) };
        for (const c of res.clamps) {
          if (c.key in ints) ints[c.key] = c.stored;
          else if (c.key in cf) cf[c.key] = c.stored;
        }
        setDraft({ ...draft, ints, class_fields: cf });
        const detailStr = res.clamps.map((c) => `${c.key} ${c.requested}→${c.stored}`).join(", ");
        setMsg(`Saved — clamped: ${detailStr}. Backup ${res.backup_id ?? ""}.`);
      } else {
        setMsg(`Saved. Backup ${res.backup_id ?? ""}.`);
      }
      await qc.invalidateQueries({ queryKey: ["items"] });
      await qc.invalidateQueries({ queryKey: ["item", selected] });
    } catch (e) {
      setMsg(`Save failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  function revert() {
    if (detail.data) setDraft(detail.data);
    setMsg("");
  }

  // ── Keyboard: Ctrl+S saves; ↑/↓ move selection (only when not dirty) ─────
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
        return;
      }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (isDirty) return; // don't arrow away from unsaved edits
      e.preventDefault();
      const idx = rows.findIndex((r) => r.ui_index === selected);
      const nextIdx = e.key === "ArrowDown"
        ? Math.min((idx < 0 ? -1 : idx) + 1, rows.length - 1)
        : Math.max(idx - 1, 0);
      const next = rows[nextIdx];
      if (next) setSelected(next.ui_index);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, selected, isDirty, writable, saving, draft]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex h-full gap-3 p-3">
      <div className="w-80 flex flex-col">
        <h1 className="font-bold text-wasteland-100 mb-2">Items</h1>
        <CategoryTabs categories={categories} active={activeCategory} onSelect={requestCategory} />
        <div className="flex gap-2 mb-2">
          <input className="input flex-1" placeholder="Search name or id…"
                 value={q} onChange={(e) => setQ(e.target.value)} />
          <select className="input w-24" value={sortKey}
                  onChange={(e) => setSortKey(e.target.value as SortKey)} title="Sort by">
            <option value="id">ID</option>
            <option value="name">Name</option>
            <option value="price">Price</option>
            <option value="coolness">Cool</option>
          </select>
        </div>
        <p className="text-[11px] text-wasteland-500 mb-1">
          Showing {Math.min(rows.length, 400)} of {rows.length}
          {activeCategory !== "all" && ` (${allItems.length} total)`}
          {!writable && " · read-only install"}
        </p>
        <div className="flex-1 overflow-y-auto border border-wasteland-700 rounded">
          {rows.length === 0 && (
            <p className="text-xs text-wasteland-500 p-3 text-center">No items match.</p>
          )}
          {rows.slice(0, 400).map((it: ItemSummary) => (
            <button key={it.ui_index}
              onClick={() => requestSelect(it.ui_index)}
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
            <div className="flex items-center justify-between sticky top-0 bg-wasteland-950/80 backdrop-blur py-1 z-10">
              <h2 className="font-bold text-wasteland-100">
                #{draft.ui_index} {draft.strings["szItemName"] ?? ""}
                {isDirty && <span className="text-rust-400" title="Unsaved changes"> *</span>}
              </h2>
              <div className="flex items-center gap-2">
                {isDirty && (
                  <button className="btn-secondary text-xs" disabled={saving} onClick={revert}>
                    Revert
                  </button>
                )}
                <button
                  className="btn-primary text-xs"
                  disabled={saving || !isDirty || !writable}
                  title={!writable ? "This install is read-only" : "Save (Ctrl+S)"}
                  onClick={save}
                >
                  {saving ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
            {msg && <p className="text-xs text-rust-300">{msg}</p>}
            {!writable && (
              <p className="text-xs text-wasteland-500">
                This install is read-only — edits can't be saved.
              </p>
            )}
            <ItemCommonForm
              schema={list.data?.common_schema ?? []}
              strings={draft.strings}
              ints={draft.ints}
              enumOptions={draft.enum_options}
              classLabel={draft.class_label}
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
                enumOptions={draft.enum_options}
                onChange={(k, v) => setDraft({
                  ...draft,
                  class_fields: { ...(draft.class_fields ?? {}), [k]: v },
                })}
              />
            )}
          </div>
        )}
      </div>

      <ConfirmModal
        open={pendingNav !== null}
        title="Discard unsaved changes?"
        body="You have unsaved edits to this item. Switching away will discard them."
        confirmLabel="Discard"
        cancelLabel="Keep editing"
        destructive
        onConfirm={applyPending}
        onCancel={() => setPendingNav(null)}
      />
    </div>
  );
}
