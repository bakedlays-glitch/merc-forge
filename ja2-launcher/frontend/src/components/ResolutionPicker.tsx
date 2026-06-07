import { useState } from "react";

// PORTABLE: pure-React, no Tauri imports.
// Codes per JA2 1.13's SCREEN_RESOLUTION constant table in Ja2.ini.

const RESOLUTIONS: { code: number; label: string }[] = [
  { code: 4,  label: "1280 × 720 (HD, safe default)" },
  { code: 5,  label: "1024 × 768 (legacy 4:3)" },
  { code: 11, label: "1600 × 900" },
  { code: 19, label: "1680 × 1050" },
  { code: 20, label: "1920 × 1080 (Full HD)" },
  { code: 22, label: "1920 × 1200" },
  { code: 23, label: "2560 × 1440 (QHD)" },
  { code: 24, label: "2560 × 1600" },
];

interface Props {
  initialCode?: number;
  onChange: (code: number) => void;
}

export function ResolutionPicker({ initialCode = 4, onChange }: Props) {
  const [code, setCode] = useState(initialCode);
  return (
    <label className="flex items-center gap-3 text-sm">
      <span className="text-ja2-dim">Resolution:</span>
      <select
        value={code}
        onChange={(e) => {
          const next = Number(e.target.value);
          setCode(next);
          onChange(next);
        }}
        className="bg-ja2-bg border border-ja2-border text-ja2-text rounded px-2 py-1
                   focus:outline-none focus:border-ja2-accent"
      >
        {RESOLUTIONS.map((r) => (
          <option key={r.code} value={r.code}>
            {r.label}
          </option>
        ))}
      </select>
    </label>
  );
}
