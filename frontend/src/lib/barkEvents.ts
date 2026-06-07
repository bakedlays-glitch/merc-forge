/**
 * JA2 merc voice "bark" events (the DialogQuoteIDs enum, Tactical/Dialogue
 * Control.h). The engine plays a merc's voice from
 *   Speech/<usVoiceIndex:03d>_<quote:03d>.<ext>
 * (verified in Dialogue Control.cpp: sprintf("SPEECH\\%03d_%03d", usVoiceSet,
 * usQuoteNum), where usVoiceSet = the profile's usVoiceIndex). These `value`s
 * are the quote numbers; the enum's own //10 //20 //30 //40 position markers
 * confirm them (and the `= QUOTE_AIM_KILLED_MIKE` alias at 32 does NOT
 * increment). Labels are cleaned from the engine's inline comments.
 *
 * Covers the recordable 0..49 range a merc actually voices in combat/morale.
 * The picker also offers "keep filename" for clips already named correctly or
 * for events beyond this list.
 */
export interface BarkEvent {
  value: number;
  label: string;
}

export const BARK_EVENTS: ReadonlyArray<BarkEvent> = [
  { value: 0, label: "See enemy" },
  { value: 1, label: "See enemy (first time in sector)" },
  { value: 2, label: "Outnumbered / in battle" },
  { value: 3, label: "See creature" },
  { value: 4, label: "See creature (first time)" },
  { value: 5, label: "Traces of creature attack" },
  { value: 6, label: "Heard something" },
  { value: 7, label: "Smelled creature" },
  { value: 8, label: "Wary / suspicious" },
  { value: 9, label: "Worried about creatures" },
  { value: 10, label: "Attacked by multiple creatures" },
  { value: 11, label: "Spotted an item" },
  { value: 12, label: "Spotted an item (alt)" },
  { value: 13, label: "Out of ammo" },
  { value: 14, label: "Seriously wounded (bleeding out)" },
  { value: 15, label: "Buddy 1 killed" },
  { value: 16, label: "Buddy 2 killed" },
  { value: 17, label: "Liked merc killed" },
  { value: 19, label: "Gun jammed" },
  { value: 20, label: "Under heavy fire" },
  { value: 21, label: "Took a beating this turn" },
  { value: 22, label: "Close call" },
  { value: 23, label: "No line of fire" },
  { value: 24, label: "Starting to bleed" },
  { value: 25, label: "Need sleep" },
  { value: 26, label: "Out of breath" },
  { value: 27, label: "Killed an enemy" },
  { value: 28, label: "Killed a creature" },
  { value: 29, label: "Complain about hated merc 1" },
  { value: 30, label: "Complain about hated merc 2" },
  { value: 31, label: "Complain about learn-to-hate merc" },
  { value: 32, label: "Killed Mike (AIM) / quit over hated merc (MERC)" },
  { value: 33, label: "Headshot / gore reaction" },
  { value: 34, label: "Disability kicks in" },
  { value: 35, label: "Assignment complete" },
  { value: 36, label: "Refusing an order" },
  { value: 37, label: "Killed Deidranna" },
  { value: 38, label: "Killed the Queen" },
  { value: 39, label: "Dislike NPC just talked to" },
  { value: 40, label: "Low morale / whining" },
  { value: 42, label: "Air raid spotted" },
  { value: 43, label: "Complain about equipment" },
  { value: 46, label: "Gained stats (level up)" },
  { value: 47, label: "Refuse to eat food" },
  { value: 48, label: "Refuse to smoke" },
  { value: 49, label: "Hated merc 1 arrives" },
];
