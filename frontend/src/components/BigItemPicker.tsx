// frontend/src/components/BigItemPicker.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { listBigItems, bigItemGraphicUrl, type BigItemGraphic } from "../lib/api";

interface Props {
  value: { type: number; num: number };
  onPick: (g: { type: number; num: number }) => void;
  onClose: () => void;
}

/**
 * One grid cell. The sprite is only fetched once the cell scrolls into view
 * (IntersectionObserver) — with ~1649 graphics this is what prevents a fetch
 * storm of one authenticated request per sprite on open. Cell is a fixed,
 * uniform size with crisp (pixelated) scaling so all click targets match and
 * small sprites stay sharp; true native size lives in the preview pane.
 */
function Cell({ g, selected, onPick, onFocusGraphic }: {
  g: BigItemGraphic;
  selected: boolean;
  onPick: () => void;
  onFocusGraphic: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [src, setSrc] = useState("");
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => { if (entries.some((e) => e.isIntersecting)) setVisible(true); },
      { rootMargin: "150px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    bigItemGraphicUrl(g.type, g.num).then((u) => { if (alive) setSrc(u); }).catch(() => {});
    return () => { alive = false; };
  }, [visible, g.type, g.num]);

  return (
    <button
      ref={ref}
      onClick={onPick}
      onMouseEnter={onFocusGraphic}
      onFocus={onFocusGraphic}
      title={g.stem}
      className={`flex items-center justify-center w-14 h-14 border rounded shrink-0 ${
        selected ? "border-rust-400 ring-1 ring-rust-400 bg-wasteland-800" : "border-wasteland-700"
      }`}
    >
      {src
        ? <img src={src} alt={g.stem} className="max-w-full max-h-full object-contain"
               style={{ imageRendering: "pixelated" }} />
        : <span className="text-[9px] text-wasteland-600">{g.num}</span>}
    </button>
  );
}

function Preview({ g }: { g: BigItemGraphic | null }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    if (!g) { setSrc(""); return; }
    let alive = true;
    bigItemGraphicUrl(g.type, g.num).then((u) => { if (alive) setSrc(u); }).catch(() => {});
    return () => { alive = false; };
  }, [g]);
  return (
    <div className="w-40 shrink-0 border-l border-wasteland-700 pl-3 flex flex-col items-center gap-2">
      <div className="flex items-center justify-center w-32 h-32 border border-wasteland-700 rounded bg-wasteland-900">
        {src
          ? <img src={src} alt={g?.stem ?? ""}
                 className="max-w-full max-h-full object-contain"
                 style={{ imageRendering: "pixelated", transform: "scale(2)" }} />
          : <span className="text-xs text-wasteland-600">hover a sprite</span>}
      </div>
      {g && (
        <div className="text-center">
          <div className="text-xs text-wasteland-200">{g.stem}</div>
          <div className="text-[11px] text-wasteland-500">type {g.type}, num {g.num}</div>
        </div>
      )}
    </div>
  );
}

export default function BigItemPicker({ value, onPick, onClose }: Props) {
  const [graphics, setGraphics] = useState<BigItemGraphic[]>([]);
  const [q, setQ] = useState("");
  const [focused, setFocused] = useState<BigItemGraphic | null>(null);

  useEffect(() => { listBigItems().then((r) => setGraphics(r.graphics)); }, []);

  // Default the preview to the item's current graphic.
  useEffect(() => {
    if (focused || graphics.length === 0) return;
    const cur = graphics.find((g) => g.type === value.type && g.num === value.num);
    if (cur) setFocused(cur);
  }, [graphics, value.type, value.num, focused]);

  const filtered = useMemo(
    () => graphics.filter((g) => g.stem.toLowerCase().includes(q.toLowerCase())),
    [graphics, q],
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="card max-w-4xl w-full max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-bold text-wasteland-100">Pick a graphic</h2>
          <button className="btn-secondary text-xs" onClick={onClose}>Close</button>
        </div>
        <input className="input mb-2" placeholder="Search (e.g. gun, p1item)…"
               value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="flex gap-3 min-h-0 flex-1">
          <div className="flex flex-wrap gap-1 overflow-y-auto content-start flex-1">
            {filtered.map((g) => (
              <Cell
                key={g.stem}
                g={g}
                selected={g.type === value.type && g.num === value.num}
                onPick={() => onPick({ type: g.type, num: g.num })}
                onFocusGraphic={() => setFocused(g)}
              />
            ))}
          </div>
          <Preview g={focused} />
        </div>
        <p className="text-[11px] text-wasteland-500 mt-2">{filtered.length} graphics</p>
      </div>
    </div>
  );
}
