// frontend/src/components/BigItemPicker.tsx
import { useEffect, useMemo, useState } from "react";
import { listBigItems, bigItemGraphicUrl, type BigItemGraphic } from "../lib/api";

interface Props {
  value: { type: number; num: number };
  onPick: (g: { type: number; num: number }) => void;
  onClose: () => void;
}

function Thumb({ g, selected, onClick }: {
  g: BigItemGraphic; selected: boolean; onClick: () => void;
}) {
  const [src, setSrc] = useState<string>("");
  useEffect(() => {
    let alive = true;
    bigItemGraphicUrl(g.type, g.num).then((u) => { if (alive) setSrc(u); });
    return () => { alive = false; };
  }, [g.type, g.num]);
  return (
    <button
      onClick={onClick}
      title={g.stem}
      className={`flex flex-col items-center p-1 border rounded ${
        selected ? "border-rust-400 bg-wasteland-800" : "border-wasteland-700"
      }`}
    >
      {src ? <img src={src} alt={g.stem} className="h-10 object-contain" /> : <div className="h-10" />}
      <span className="text-[10px] text-wasteland-400 truncate w-16">{g.stem}</span>
    </button>
  );
}

export default function BigItemPicker({ value, onPick, onClose }: Props) {
  const [graphics, setGraphics] = useState<BigItemGraphic[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => { listBigItems().then((r) => setGraphics(r.graphics)); }, []);
  const filtered = useMemo(
    () => graphics.filter((g) => g.stem.toLowerCase().includes(q.toLowerCase())),
    [graphics, q],
  );
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
         onClick={onClose}>
      <div className="card max-w-2xl w-full max-h-[80vh] flex flex-col"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-bold text-wasteland-100">Pick a graphic</h2>
          <button className="btn-secondary text-xs" onClick={onClose}>Close</button>
        </div>
        <input
          className="input mb-2" placeholder="Search (e.g. gun, p1item)…"
          value={q} onChange={(e) => setQ(e.target.value)}
        />
        <div className="grid grid-cols-8 gap-1 overflow-y-auto">
          {filtered.map((g) => (
            <Thumb
              key={g.stem}
              g={g}
              selected={g.type === value.type && g.num === value.num}
              onClick={() => onPick({ type: g.type, num: g.num })}
            />
          ))}
        </div>
        <p className="text-[11px] text-wasteland-500 mt-2">{filtered.length} graphics</p>
      </div>
    </div>
  );
}
