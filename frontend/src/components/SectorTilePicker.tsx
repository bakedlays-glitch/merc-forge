/**
 * Visual tile picker for RPC placement — renders a sector as an iso PNG
 * (MapForge's /sector/render) and turns a click into a JA2 gridno.
 *
 * Reuses MapForge's pure projection math (imagePixelToTile / tileToCanvasPixel)
 * over a flat PNG — no WebGL renderer needed. gridno = y * 160 + x (JA2 world
 * is a fixed 160 tiles wide); matches the hand-authored InitialProfile gridnos.
 */
import { useEffect, useRef, useState } from "react";

import {
  fetchSectorRender,
  getSectorInfo,
  imagePixelToTile,
  listInstallMaps,
  tileToCanvasPixel,
  type RenderMeta,
} from "../lib/mapforge";

const WORLD_COLS = 160;

function sectorFileMatches(fileName: string, code: string): boolean {
  return fileName.replace(/\.dat$/i, "").toUpperCase() === code.trim().toUpperCase();
}

export default function SectorTilePicker({
  sector,
  currentGridno,
  onPick,
  onClose,
}: {
  sector: string;
  currentGridno?: number;
  onPick: (gridno: number, tile: { x: number; y: number }) => void;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [errMsg, setErrMsg] = useState("");
  const [url, setUrl] = useState<string | null>(null);
  const [meta, setMeta] = useState<RenderMeta | null>(null);
  const [dims, setDims] = useState<{ cols: number; rows: number } | null>(null);
  const [picked, setPicked] = useState<{ x: number; y: number; gridno: number } | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPhase("loading");
    setErrMsg("");
    (async () => {
      try {
        const maps = await listInstallMaps();
        const map = maps.maps.find((m) => sectorFileMatches(m.name, sector));
        if (!map) {
          throw new Error(`No .dat file for sector ${sector.trim().toUpperCase()} in this install.`);
        }
        const info = await getSectorInfo(map.path);
        const r = await fetchSectorRender({
          datPath: map.path,
          xmlPath: maps.ja2set_xml ?? "",
          tileset: info.tileset_in_header,
          full: true,
        });
        if (cancelled) {
          URL.revokeObjectURL(r.url);
          return;
        }
        urlRef.current = r.url;
        setUrl(r.url);
        setMeta(r.meta);
        setDims({ cols: info.cols, rows: info.rows });
        if (currentGridno != null && currentGridno >= 0) {
          setPicked({
            x: currentGridno % WORLD_COLS,
            y: Math.floor(currentGridno / WORLD_COLS),
            gridno: currentGridno,
          });
        }
        setPhase("ready");
      } catch (e) {
        if (!cancelled) {
          setErrMsg(e instanceof Error ? e.message : String(e));
          setPhase("error");
        }
      }
    })();
    return () => {
      cancelled = true;
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    };
  }, [sector, currentGridno]);

  function handleClick(e: React.MouseEvent<HTMLImageElement>) {
    if (!meta || !dims || !imgRef.current) return;
    const rect = imgRef.current.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * meta.canvasW;
    const py = ((e.clientY - rect.top) / rect.height) * meta.canvasH;
    const tile = imagePixelToTile(px, py, meta, dims.cols, dims.rows);
    if (!tile) return;
    setPicked({ ...tile, gridno: tile.y * WORLD_COLS + tile.x });
  }

  // Marker as a percentage of the (max-width-scaled) image — resolution-independent.
  let marker: { left: string; top: string } | null = null;
  if (picked && meta) {
    const p = tileToCanvasPixel(picked.x, picked.y, meta);
    marker = {
      left: `${(p.x / meta.canvasW) * 100}%`,
      top: `${((p.y - meta.tileH / 2) / meta.canvasH) * 100}%`,
    };
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="card max-h-[90vh] max-w-[90vw] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-2 flex items-center justify-between gap-4">
          <h3 className="text-sm font-semibold text-wasteland-200">
            Pick a tile — sector {sector.trim().toUpperCase()}
          </h3>
          <button className="btn-ghost text-xs" onClick={onClose}>
            Close
          </button>
        </div>

        {phase === "loading" && <p className="text-xs text-wasteland-400">Rendering sector…</p>}
        {phase === "error" && <p className="text-xs text-rust-300">{errMsg}</p>}

        {phase === "ready" && url && (
          <>
            <div className="relative inline-block">
              <img
                ref={imgRef}
                src={url}
                onClick={handleClick}
                className="max-w-full cursor-crosshair select-none"
                alt={`Sector ${sector}`}
              />
              {marker && (
                <div
                  className="pointer-events-none absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-rust-400 bg-rust-500/40"
                  style={marker}
                />
              )}
            </div>
            <div className="mt-2 flex items-center justify-between gap-4">
              <span className="font-mono text-xs text-wasteland-300">
                {picked
                  ? `tile (${picked.x}, ${picked.y}) → gridno ${picked.gridno}`
                  : "Click a tile"}
              </span>
              <button
                className="btn-primary text-xs"
                disabled={!picked}
                onClick={() => {
                  if (picked) {
                    onPick(picked.gridno, { x: picked.x, y: picked.y });
                    onClose();
                  }
                }}
              >
                Use this tile
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
