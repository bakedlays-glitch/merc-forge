import { categoryLabel, type SlotCategory } from "./slotPicker";

export interface RelocateNotice {
  severity: "info" | "warn";
  text: string;
}

/**
 * The "what changes at the destination" notice shared by the Copy
 * (Duplicate) and Cut (Move) flows. Two flavors:
 *
 *   - severity: "info"  → blue, positive ("here's what we'll do for you")
 *   - severity: "warn"  → yellow, attention-needed ("you might not want this")
 *
 * Pre-#90 this was always a yellow "Slot category change" warning even
 * for the routine case where the merc WILL appear on AIM (because
 * relocator.duplicate auto-writes the row, see
 * sidecar/mercwizard_core/relocator.py:290-297) — internally
 * contradictory and the #1 source of Duplicate-flow confusion.
 *
 * The case table is identical for both flows (the dest-side effects are
 * the same whether the source survives); only the phrasing differs, so
 * the verb/subject strings switch on `mode`.
 */
export function buildRelocateNotice(args: {
  mode: "copy" | "move";
  sourceClass: SlotCategory | null;
  destClass: SlotCategory | null;
  source: number | null;
  dest: number | null;
  sourceName: string;
  sourceType: number | null;
}): RelocateNotice | null {
  const { mode, sourceClass, destClass, source, dest, sourceName, sourceType } = args;
  if (sourceClass === null || destClass === null) return null;

  const isCopy = mode === "copy";
  // "the copy" for Duplicate; the merc's own name for Move.
  const subject = isCopy ? "the copy" : sourceName;

  // Type=1 (AIM) source landing in an unassigned slot. The relocator
  // unconditionally writes a fresh AIMAvailability row for the dest, so
  // the merc IS hireable on AIM after the write. No warning — but the
  // user benefits from a confirmation since the dest looked "empty".
  if (sourceType === 1 && destClass === "unassigned") {
    return {
      severity: "info",
      text: `Slot ${dest} isn't currently on the AIM roster — MercForge will register ${
        isCopy ? "it" : sourceName + " there"
      } automatically so ${subject} ${isCopy ? "is" : "stays"} hireable on AIM. (A fresh AimBioID ${
        isCopy ? "+ bio entry are written at the same time" : `is computed; the old AIM row at slot ${source} is removed`
      }.)`,
    };
  }

  // Type=2 (M.E.R.C. / Speck's) source landing in an unassigned slot.
  // Same auto-write story as AIM but for MercAvailability.xml.
  if (sourceType === 2 && destClass === "unassigned") {
    return {
      severity: "info",
      text: `Slot ${dest} isn't currently on Speck's M.E.R.C. roster — MercForge will register ${
        isCopy ? "it" : sourceName + " there"
      } automatically${isCopy ? " so the copy shows up on the M.E.R.C. website" : ""}. (A fresh MercBioID ${
        isCopy ? "+ bio entry are written at the same time" : `is computed; the old row at slot ${source} is removed`
      }.)`,
    };
  }

  // AIM source landing on a slot with a leftover M.E.R.C. row from a
  // previous occupant (rare; usually merc_availability.remove cleans it
  // up). The write would leave the merc on BOTH hire lists.
  if (sourceType === 1 && destClass === "merc") {
    return {
      severity: "warn",
      text: `Slot ${dest} still has a leftover M.E.R.C. row from a previous occupant. Afterwards, ${subject} would appear on BOTH AIM (new row) and M.E.R.C. (stale row) — usually not what you want. Pick an unassigned slot, or clean up MercAvailability.xml at ${dest} first.`,
    };
  }

  // Mirror case: M.E.R.C. source onto a leftover AIM row.
  if (sourceType === 2 && destClass === "aim") {
    return {
      severity: "warn",
      text: `Slot ${dest} has a leftover AIM row from a previous occupant. Afterwards, ${subject} would appear on BOTH M.E.R.C. (new row) and AIM (stale row) — pick an unassigned slot, or clear AIMAvailability.xml at ${dest} first.`,
    };
  }

  // RPC / NPC source (Type 3/4) — the result inherits the unhireable
  // Type regardless of the dest slot's category, so even if dest is
  // categorized "aim" it won't show up on the AIM laptop. This
  // genuinely surprises people.
  if ((sourceType === 3 || sourceType === 4)
      && (destClass === "aim" || destClass === "merc")) {
    const typeLabel = sourceType === 3 ? "RPC" : "NPC";  // engine: RPC=3, NPC=4
    const site = destClass === "aim" ? "AIM website" : "M.E.R.C. website (Speck's service)";
    return {
      severity: "warn",
      text: `${sourceName} is ${typeLabel} (scripted). ${
        isCopy ? `The duplicate stays ${typeLabel}` : `The move keeps Type=${typeLabel}`
      }, so ${isCopy ? "it" : "they"} WON'T appear on the ${site} even though slot ${dest} has a row there. Change Type to 1 (AIM) or 2 (M.E.R.C.) ${
        isCopy ? "on the duplicate after the copy" : "after the move"
      } if you want ${isCopy ? "it" : "them"} hireable.`,
    };
  }

  // AIM source landing in the engine's named RPC/NPC range — quest
  // scripts may call the slot by name.
  if (sourceType === 1 && (destClass === "rpc" || destClass === "npc")) {
    return {
      severity: "warn",
      text: `Slot ${dest} is in the engine's named ${destClass.toUpperCase()} range. Quest scripts may call this slot by name — putting ${sourceName} here redirects whatever scripted dialogue used to play for the original occupant.`,
    };
  }

  // Same-category → nothing surprising, skip the notice.
  if (sourceClass === destClass) return null;

  // Generic fallback for any combination not enumerated above.
  return {
    severity: "info",
    text: `Slot category changes from ${categoryLabel(sourceClass)} to ${categoryLabel(destClass)}. ${
      isCopy ? "The duplicate's" : `${sourceName}'s`
    } Type stays the same (${sourceType ?? "?"}); MercForge writes whichever XML rows are needed so ${
      isCopy ? "the copy lands in" : "they stay on"
    } the same hire list.`,
  };
}
