import type { Merc } from "../../lib/schema";

/**
 * Tactical-sprite palette codes the engine uses to tint the merc's
 * tactical sprite (NOT the portrait — portrait colors are baked into
 * the STI). Pants/Vest/Skin/Hair are independent. Custom palette codes
 * (mod-defined) are allowed by the engine; we only offer the vanilla
 * set in this dropdown to keep the UI scannable. Users wanting a mod
 * code can punch it in via the raw XML or by editing MercProfiles.xml
 * directly — round-tripping preserves unknown codes.
 *
 * Extracted from Create.tsx 2026-05-24 so Edit.tsx can use the same
 * fieldset without duplicating the palette enum. User feedback: "edit
 * seems to be missing a bunch of stuff you could edit?"
 */

const PANTS_CODES = [
  "TANPANTS", "BLUEPANTS", "JEANPANTS", "BEIGEPANTS",
  "BLACKPANTS", "GREENPANTS", "BROWNPANTS",
] as const;
const VEST_CODES = [
  "BROWNVEST", "BLUEVEST", "WHITEVEST", "GREENVEST",
  "YELLOWVEST", "REDVEST", "BLACKVEST", "PURPLESHIRT",
] as const;
const SKIN_CODES = ["PINKSKIN", "TANSKIN", "DARKSKIN", "BLACKSKIN"] as const;
const HAIR_CODES = ["BROWNHEAD", "BLACKHEAD", "WHITEHEAD", "BLONDHEAD", "REDHEAD"] as const;

/** Approximate hex preview for each palette code. The engine actually
 * uses a tactical sprite palette swap, NOT a literal RGB tint — these
 * hex values are a rough visual cue so users can pick a color by sight
 * instead of by remembering the code. The displayed swatch is for
 * recognition only; the engine renders the real palette at runtime. */
const PALETTE_HEX: Record<string, string> = {
  // Pants
  TANPANTS: "#c19a6b",
  BLUEPANTS: "#2e4a82",
  JEANPANTS: "#5470a3",
  BEIGEPANTS: "#d4b896",
  BLACKPANTS: "#1a1a1a",
  GREENPANTS: "#4a6741",
  BROWNPANTS: "#5d3f24",
  // Vest
  BROWNVEST: "#5d3f24",
  BLUEVEST: "#2e4a82",
  WHITEVEST: "#e8e8e8",
  GREENVEST: "#4a6741",
  YELLOWVEST: "#c9b430",
  REDVEST: "#8a2828",
  BLACKVEST: "#1a1a1a",
  PURPLESHIRT: "#5a3777",
  // Skin
  PINKSKIN: "#e8c7b8",
  TANSKIN: "#c8a07b",
  DARKSKIN: "#8a5d3a",
  BLACKSKIN: "#3d2818",
  // Hair
  BROWNHEAD: "#5d3f24",
  BLACKHEAD: "#0e0e0e",
  WHITEHEAD: "#e8e8e8",
  BLONDHEAD: "#d4b54e",
  REDHEAD: "#a64a2e",
};

/** Strip the trailing "PANTS" / "VEST" / "SKIN" / "HEAD" / "SHIRT"
 * suffix so chip labels show just the color name. Keeps chips
 * narrow + scannable. */
function shortLabel(code: string): string {
  return code
    .replace(/PANTS$/, "")
    .replace(/VEST$/, "")
    .replace(/SKIN$/, "")
    .replace(/HEAD$/, "")
    .replace(/SHIRT$/, "");
}

export interface AppearancePaletteFormProps {
  merc: Merc;
  /** Field-level setter — keeps the parent route in charge of the
   *  immutable update. Mirrors the (field, value) shape used by the
   *  other extracted form components. */
  onChange: <K extends keyof Merc>(field: K, value: Merc[K]) => void;
}

function PaletteChips<K extends keyof Merc>({
  label, current, codes, onPick,
}: {
  label: string;
  current: string;
  codes: readonly string[];
  onPick: (code: Merc[K]) => void;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-xs text-wasteland-300">{label}</span>
        <span className="text-[10px] font-mono text-wasteland-500">
          {current}
        </span>
      </div>
      <div className="flex flex-wrap gap-1">
        {codes.map((c) => {
          const hex = PALETTE_HEX[c] ?? "#666";
          const active = c === current;
          return (
            <button
              key={c}
              type="button"
              onClick={() => onPick(c as Merc[K])}
              title={`${c} — click to set ${label.toLowerCase()}`}
              className={`flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[10px] transition-all ${
                active
                  ? "border-rust-500 bg-rust-500/10 text-rust-100"
                  : "border-wasteland-700 bg-wasteland-900 text-wasteland-300 hover:border-wasteland-500"
              }`}
            >
              <span
                className="inline-block h-3 w-3 rounded-sm border border-black/40"
                style={{ backgroundColor: hex }}
              />
              {shortLabel(c)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function AppearancePaletteForm({
  merc, onChange,
}: AppearancePaletteFormProps) {
  return (
    <fieldset className="block border border-wasteland-700 rounded p-3">
      <legend className="text-sm font-medium text-wasteland-100 px-1">
        Sprite palette (tactical view colors)
      </legend>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
        <PaletteChips<"PANTS">
          label="Pants"
          current={merc.PANTS}
          codes={PANTS_CODES}
          onPick={(c) => onChange("PANTS", c)}
        />
        <PaletteChips<"VEST">
          label="Vest"
          current={merc.VEST}
          codes={VEST_CODES}
          onPick={(c) => onChange("VEST", c)}
        />
        <PaletteChips<"SKIN">
          label="Skin"
          current={merc.SKIN}
          codes={SKIN_CODES}
          onPick={(c) => onChange("SKIN", c)}
        />
        <PaletteChips<"HAIR">
          label="Hair"
          current={merc.HAIR}
          codes={HAIR_CODES}
          onPick={(c) => onChange("HAIR", c)}
        />
      </div>
      <p className="text-xs text-wasteland-400 mt-2">
        These tint the merc's tactical sprite (not the portrait). The
        chip colors are an approximate preview — the engine swaps
        palettes at runtime, so what you see in-game may shift.
      </p>
    </fieldset>
  );
}
