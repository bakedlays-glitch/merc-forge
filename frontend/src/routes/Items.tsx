import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listItems, getItem, updateItem, itemGraphicUrl,
  type ItemSummary, type ItemDetail,
} from "../lib/api";
import ItemCommonForm from "../components/forms/ItemCommonForm";
import ItemClassStatsForm from "../components/forms/ItemClassStatsForm";
import CategoryTabs from "../components/items/CategoryTabs";
import ConfirmModal from "../components/ConfirmModal";

/**
 * Item graphic that only fetches once it scrolls into view (IntersectionObserver).
 * The browser grid can show hundreds of cards; eager-fetching every one would be a
 * request storm, so each thumbnail loads lazily.
 */
function LazyThumb({ id, className }: { id: number; className: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [src, setSrc] = useState("");
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => { if (entries.some((e) => e.isIntersecting)) setVisible(true); },
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    let alive = true;
    itemGraphicUrl(id).then((u) => { if (alive) setSrc(u); }).catch(() => {});
    return () => { alive = false; };
  }, [visible, id]);
  return (
    <div ref={ref} className={`flex items-center justify-center ${className}`}>
      {src && (
        <img src={src} alt="" className="max-w-full max-h-full object-contain"
             style={{ imageRendering: "pixelated" }}
             onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")} />
      )}
    </div>
  );
}

type SortKey = "id" | "name" | "price" | "coolness";

function editableEq(a: ItemDetail | null, b: ItemDetail | null): boolean {
  if (!a || !b) return a === b;
  const pick = (d: ItemDetail) =>
    JSON.stringify({ s: d.strings, i: d.ints, c: d.class_fields ?? {} });
  return pick(a) === pick(b);
}

const ITEM_CAP = 600;

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
  const [pendingNav, setPendingNav] = useState<
    { kind: "select"; id: number } | { kind: "category"; key: string } | { kind: "back" } | null
  >(null);

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
  }
  function requestBack() {
    if (isDirty) { setPendingNav({ kind: "back" }); return; }
    setSelected(null);
    setMsg("");
  }
  function applyPending() {
    if (!pendingNav) return;
    if (pendingNav.kind === "select") setSelected(pendingNav.id);
    else if (pendingNav.kind === "category") { setActiveCategory(pendingNav.key); setSelected(null); }
    else { setSelected(null); setMsg(""); }
    setPendingNav(null);
  }

  async function save() {
    if (!draft || selected === null || !isDirty || !writable) return;
    setSaving(true);
    setMsg("");
    try {
      const res = await updateItem(selected, {
        strings: draft.strings, ints: draft.ints, class_fields: draft.class_fields ?? {},
      });
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

  // Ctrl+S saves; ↑/↓ move to the prev/next item while editing (dirty-guarded).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
        return;
      }
      if (selected === null) return;
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (isDirty) return;
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

  const guard = (
    <ConfirmModal
      open={pendingNav !== null}
      title="Discard unsaved changes?"
      body="You have unsaved edits to this item. Leaving will discard them."
      confirmLabel="Discard"
      cancelLabel="Keep editing"
      destructive
      onConfirm={applyPending}
      onCancel={() => setPendingNav(null)}
    />
  );

  // ── EDITOR VIEW ─────────────────────────────────────────────────────────
  if (selected !== null) {
    return (
      <div className="h-full flex flex-col p-3">
        <div className="flex items-center justify-between sticky top-0 bg-wasteland-950/90 backdrop-blur py-2 z-10 border-b border-wasteland-800">
          <div className="flex items-center gap-3">
            <button className="btn-secondary text-xs" onClick={requestBack}>← Items</button>
            <h2 className="font-bold text-wasteland-100">
              #{draft?.ui_index ?? selected} {draft?.strings["szItemName"] ?? ""}
              {isDirty && <span className="text-rust-400" title="Unsaved changes"> *</span>}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            {isDirty && (
              <button className="btn-secondary text-xs" disabled={saving} onClick={revert}>Revert</button>
            )}
            <button className="btn-primary text-xs" disabled={saving || !isDirty || !writable}
              title={!writable ? "This install is read-only" : "Save (Ctrl+S)"} onClick={save}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
        {msg && <p className="text-xs text-rust-300 mt-2">{msg}</p>}
        {!writable && (
          <p className="text-xs text-wasteland-500 mt-1">This install is read-only — edits can't be saved.</p>
        )}
        <div className="flex-1 overflow-y-auto mt-2">
          {!draft ? (
            <p className="text-wasteland-500 text-sm">Loading…</p>
          ) : (
            <div className="max-w-3xl mx-auto space-y-3">
              <ItemCommonForm
                schema={list.data?.common_schema ?? []}
                strings={draft.strings}
                ints={draft.ints}
                enumOptions={draft.enum_options}
                classLabel={draft.class_label}
                onStr={(k, v) => setDraft({ ...draft, strings: { ...draft.strings, [k]: v } })}
                onInt={(k, v) => setDraft({ ...draft, ints: { ...draft.ints, [k]: v } })}
                onPickGraphic={(g) => setDraft({
                  ...draft, ints: { ...draft.ints, ubGraphicType: g.type, ubGraphicNum: g.num },
                })}
              />
              {draft.family && draft.class_schema && (
                <ItemClassStatsForm
                  family={draft.family}
                  schema={draft.class_schema}
                  fields={draft.class_fields ?? {}}
                  enumOptions={draft.enum_options}
                  onChange={(k, v) => setDraft({
                    ...draft, class_fields: { ...(draft.class_fields ?? {}), [k]: v },
                  })}
                />
              )}
            </div>
          )}
        </div>
        {guard}
      </div>
    );
  }

  // ── BROWSER VIEW ────────────────────────────────────────────────────────
  return (
    <div className="h-full flex flex-col p-3">
      <h1 className="font-bold text-wasteland-100 mb-2">Items</h1>
      <CategoryTabs categories={categories} active={activeCategory} onSelect={requestCategory} />
      <div className="flex gap-2 mb-2">
        <input className="input flex-1" placeholder="Search name or id…"
               value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input w-28" value={sortKey}
                onChange={(e) => setSortKey(e.target.value as SortKey)} title="Sort by">
          <option value="id">Sort: ID</option>
          <option value="name">Sort: Name</option>
          <option value="price">Sort: Price</option>
          <option value="coolness">Sort: Cool</option>
        </select>
      </div>
      <p className="text-[11px] text-wasteland-500 mb-2">
        Showing {Math.min(rows.length, ITEM_CAP)} of {rows.length}
        {activeCategory !== "all" && ` (${allItems.length} total)`}
        {!writable && " · read-only install"}
      </p>
      <div className="flex-1 overflow-y-auto">
        {rows.length === 0 && (
          <p className="text-sm text-wasteland-500 p-6 text-center">No items match.</p>
        )}
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-2">
          {rows.slice(0, ITEM_CAP).map((it: ItemSummary) => (
            <button key={it.ui_index}
              onClick={() => requestSelect(it.ui_index)}
              title={it.name}
              className="flex flex-col items-center gap-1 p-2 border border-wasteland-700 rounded hover:border-rust-400 hover:bg-wasteland-800 transition-colors">
              <LazyThumb id={it.ui_index} className="h-14 w-14" />
              <span className="text-xs text-wasteland-200 text-center leading-tight line-clamp-2 w-full">
                {it.name || "—"}
              </span>
              <div className="flex items-center justify-between w-full text-[10px] text-wasteland-500">
                <span>#{it.ui_index}</span>
                <span>${it.price}</span>
              </div>
            </button>
          ))}
        </div>
        {rows.length > ITEM_CAP && (
          <p className="text-[11px] text-wasteland-500 p-3 text-center">
            Showing first {ITEM_CAP} of {rows.length}. Refine with search or a category.
          </p>
        )}
      </div>
      {guard}
    </div>
  );
}
