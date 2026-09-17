import type { PaletteSlot } from "./mapforge";

export interface PropFrameLabel {
  primary: string;
  context: string | null;
  filename: string | null;
  technical: string;
}

/** Resolve the name of the actual painted frame, with the sheet name as context. */
export function propFrameLabel(
  slot: number,
  sub: number,
  metadata: PaletteSlot | undefined,
  fallbackFilename?: string | null,
): PropFrameLabel {
  const filename = metadata?.sti_filename || fallbackFilename || null;
  const perFrame = metadata?.sub_names?.[sub]?.trim();
  const sheet = metadata?.display_name?.trim();
  const fallback = filename
    ? filename.replace(/\.sti$/i, "").replace(/[_-]+/g, " ").trim()
    : `Slot ${slot} frame ${sub}`;
  return {
    primary: perFrame || sheet || fallback,
    context: perFrame && sheet ? sheet : null,
    filename,
    technical: `slot ${slot} · frame ${sub}`,
  };
}
