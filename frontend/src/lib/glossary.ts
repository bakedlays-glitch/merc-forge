/** Shared FieldHelp glossary — ids must match sidecar HELP_TERMS. */

export interface GlossaryEntry {
  id: string;
  title: string;
  body: string;
}

export const GLOSSARY: Record<string, GlossaryEntry> = {
  AP: {
    id: "AP",
    title: "Action points (AP)",
    body: `The points a merc spends to *move, shoot, and act* each turn.

## How the pool is built
Base APs: \`20 + (10×level + 3×Agility + 2×life max + 2×Dexterity + 5) / 10\`
Then injury (up to 2/3 off), fatigue (up to 1/2 off), and encumbrance cut it. Worn gear can add a [[flat]] bonus. Terrain / airdrop / assault backgrounds then scale: \`APs × (100+N) / 100\` — a [[percent]], *not* a flat add.

## Example
100 AP at desert +8 → **108 AP**. Activity fields (swim, fortify, inventory) instead raise the *cost* of that action.`,
  },
  CtH: {
    id: "CtH",
    title: "Chance to hit (CtH)",
    body: `The to-hit roll when attacking.

## Guns
Primary stat is [[Marksmanship]]. NCTH also folds in [[Dexterity]], [[Wisdom]], and [[Experience]]. *0 Marks or 0 Dex → minimum CtH; you will not hit.*

## Throws and blades
Grenades/knives average Dex and Marks. Launchers average Dex, Marks, Wisdom, and 10× level.

## Backgrounds
Some fields are a [[percent]] of CtH (\`CtH × (100+N) / 100\`). Others are [[flat]] points added to the roll. The field tip says which.`,
  },
  IMP: {
    id: "IMP",
    title: "I.M.P. character creation",
    body: `The in-game **Instant Mercenary Processor** — the screen where you create a custom merc.

A background is listed there only if its id is at or before the last *physical* entry in Backgrounds.xml (the IMP threshold). [[clamp|IDs 500+]] are silently dropped by the engine.`,
  },
  percent: {
    id: "percent",
    title: "Percent modifier",
    body: `Scale the current value: \`result = value × (100+N) / 100\`

## Example
100 at +10 → **110**. This is *not* a [[flat]] add of 10.

Some *cost* fields invert this (positive N = more expensive). Travel-time fields subtract N instead (\`time × (100−N) / 100\`, positive = faster).`,
  },
  flat: {
    id: "flat",
    title: "Flat modifier",
    body: `Add N directly to a score or meter.

## Example
70 [[CtH]] at +10 → **80**. A [[percent]] would have been 77.

Resistances that add "percentage-points" are flat adds to a percent-meter, *not* a percent of your current resist.`,
  },
  clamp: {
    id: "clamp",
    title: "Engine clamp",
    body: `The editor range shown under the field (e.g. \`-8..8\`). Values outside it are changed on save — a 10 in a −8..8 field *stores as 8*.

The engine also silently drops background ids of **500 or more**.`,
  },
  Strength: {
    id: "Strength",
    title: "Strength",
    body: `**Muscle.** How hard you hit, how much you haul, how far you throw — *not* hand skill ([[Dexterity]]) and *not* foot speed ([[Agility]]).

## Combat
- Melee impact and close-combat ratings
- Throwing range
- Smash-door checks
- Climbing
- Sleep-dart resist (higher STR → less likely to drop)

## Carry
Carry weight starts from effective Strength, then the separate *carry-capacity* background is a second [[percent]] on top. About half STR in kg before encumbrance (option-scaled).

## How the number is made
Half of STR is full, half is scaled by remaining life (bandaged counts half). Then disease, then this background: \`STR × (100+N) / 100\`
Range [[clamp|−10..10]]. 80 at +10 → 88.`,
  },
  Agility: {
    id: "Agility",
    title: "Agility",
    body: `**Speed and footwork.** Reflexes, not hands ([[Dexterity]]).

## Combat
- Biggest [[AP]] stat after [[Experience]] (3× in the AP formula)
- Blade ready/aim AP averages Agility and Dexterity
- Melee attack and defense ratings
- Steal-from-person (enhanced HtH) weights Agility with Dexterity

## Encumbrance
Starting the turn overweight *cuts Agility first*, which then cuts APs.

## How the number is made
Base + extras − drunk, then disease, then this background: \`AGI × (100+N) / 100\`
Range [[clamp|−10..10]].`,
  },
  Dexterity: {
    id: "Dexterity",
    title: "Dexterity",
    body: `**Hands.** Coordination and fine motor skill — *not* foot speed ([[Agility]]) and *not* shooting knowledge ([[Marksmanship]]).

## Combat
- **Action points** — 2× weight in this turn's [[AP]] pool (after [[Experience]] and Agility).
- **Guns (NCTH)** — one of four base/aim attributes (with Marks, [[Wisdom]], level). *0 DEX or 0 Marks → minimum [[CtH]]; you will not hit.* Also recoil recovery and shooting on the move.
- **Tired shots** — when [[Breath]] is low, DEX reduces the to-hit penalty (*"he can compensate"*).
- **Throws** — grenades and throwing knives: avg(DEX, Marks). Launchers: avg(DEX, Marks, Wisdom, 10× level).
- **Blades** — ready/aim AP uses avg(DEX, Agility). Guns use Marks for that instead.
- **Melee** — punch/stab attacker rating *triples* DEX. Steal uses DEX + Agility. Boxing counter-punch chance scales with DEX and breath.
- **Ducking targets** — shooter's DEX helps land a shot on someone who's dodging.

## Hands-on
- **First aid** in combat — the "handiness" term in bandage skill (with [[Medical]] and kit quality).
- **Doctor / repair / clean guns** on the map — doctoring averages DEX + Wisdom with Medical; repair is Mechanical × DEX; cleaning weights DEX 3×.
- **Lockpicking** — [[Mechanical]] scaled by \`(DEX+100)/200\` and \`(Wisdom+100)/200\`.
- **Bombs & traps** — detonators mix [[Explosives]] + DEX; planting mechanical bombs and all three disarm checks add DEX; attaching special items scales Mechanical by DEX.
- **Catch** a thrown item: \`50 + DEX/2\`.
- **Handcuff** a conscious prisoner — DEX is 2× on the attacker's roll.
- **Slip an item onto someone** (syringe etc.) — DEX vs their alertness.
- **Train Dexterity** — an instructor's teaching uses their effective DEX.

## How the number is made
Base + extras (skipped if teaching) − drunk, then disease, then this background:
\`DEX × (100+N) / 100\`
Robots can get a chassis DEX bonus. Range [[clamp|−10..10]]. Example: 80 at +10 → **88**.`,
  },
  Wisdom: {
    id: "Wisdom",
    title: "Wisdom",
    body: `**Judgment and know-how.** The "do I actually understand this" stat.

## Combat
NCTH aim and recoil terms. Notice-dart checks. Interrupt-ish Wisdom/Dex rolls when someone shoots you.

## Skills and the map
- Lockpicking and attaching kits scale Mechanical by \`(WIS+100)/200\`
- Doctoring averages Wisdom with [[Dexterity]]
- Interrogation, recruiting, militia/admin (with [[Leadership]])
- Poor Wisdom *penalizes* trap-disarm checks

Drunk cuts it. Background is a [[percent]] of effective Wisdom. Range [[clamp|−10..10]].`,
  },
  Leadership: {
    id: "Leadership",
    title: "Leadership",
    body: `**Command presence.** Talking, not shooting.

- Recruiting and NPC approach scores
- Interrogation
- Militia / admin ratings
- Lying to Deidranna (with [[Wisdom]])

*Feeling-good* drunk actually raises leadership 20% before the background [[percent]]. Range [[clamp|−10..10]].`,
  },
  Marksmanship: {
    id: "Marksmanship",
    title: "Marksmanship",
    body: `**Shooting.** How well you put rounds on a target.

## Guns
Primary gun [[CtH]]. NCTH mixes it with [[Dexterity]], [[Wisdom]], and [[Experience]]. *0 Marks (or 0 Dex) → you will not hit.* Also cheapens aiming [[AP]] for guns.

## Not guns
Launchers average Dex + Marks + Wisdom + 10× level. Throws average Dex + Marks. Blades do *not* use Marks for ready-AP (that's Dex/Agility).

Background is a [[percent]] of effective Marks. Range [[clamp|−10..10]].`,
  },
  Mechanical: {
    id: "Mechanical",
    title: "Mechanical",
    body: `**Tools and gadgets.**

- Lockpicking and electronic locks (then scaled by [[Wisdom]] and [[Dexterity]])
- Unjamming guns
- Attaching kits / special items
- Faster repair and gun-cleaning on the map
- Mechanical bombs and mechanical-trap disarm

**0 Mechanical fails those checks.** Background is a [[percent]] of effective Mechanical. Range [[clamp|−10..10]].`,
  },
  Medical: {
    id: "Medical",
    title: "Medical",
    body: `**Medicine.**

- First aid in combat (bandage skill weights Medical hardest, then kit, level, [[Dexterity]])
- Doctoring on the map: \`Medical × avg(Dex, Wisdom) × (100 + 5×level)\`

Background is a [[percent]] of effective Medical. Range [[clamp|−10..10]].`,
  },
  Explosives: {
    id: "Explosives",
    title: "Explosives",
    body: `**Bombs and traps.**

- Planting bombs / remote bombs
- Attaching detonators ([[Dexterity]] also mixes in)
- Disarming explosive and electronic traps (Mechanical too; DEX added; 0 Explosive or 0 Mechanical can fail under new traits)

The disarm-trap *background* is a separate [[flat]] add on that check. Background Explosives is a [[percent]] of the stat. Range [[clamp|−10..10]].`,
  },
  Breath: {
    id: "Breath",
    title: "Breath (energy)",
    body: `Fatigue / energy, 0–100.

Below 100, this turn's [[AP]]s are cut by up to half: \`APs − APs×(100−breath)/200\`. Low breath also hurts gun [[CtH]]; [[Dexterity]] reduces that penalty.

The Drink energy regen background is a [[percent]] of breath *gained*, not spent.`,
  },
  Stealth: {
    id: "Stealth",
    title: "Stealth",
    body: `Chance to stay unnoticed. Worn gear stealth plus this background as [[flat]] percentage-points, then capped at 100.

Example: 40 stealth at +10 → **50**.`,
  },
  Camo: {
    id: "Camo",
    title: "Camouflage",
    body: `How well the merc blends into the tile. Worn camo plus this background as [[flat]] percentage-points (harder to spot). Range [[clamp|−20..10]].`,
  },
  Survivalist: {
    id: "Survivalist",
    title: "Survivalist",
    body: `A skill trait. Tracker ability on a background is [[flat]] points added to tracker skill, then divided by 100 together with Survivalist.

Example: Survivalist 20 + tracker 20 → **0.40** tracking.`,
  },
  SAM: {
    id: "SAM",
    title: "SAM site",
    body: `Surface-to-air missile sites on the strategic map.

The SAM CtH background is a [[percent]] of that site's chance-to-hit: \`CtH × (100+N) / 100\`. Tropical SAM tiles also stack the Tropical [[AP]] terrain bonus.`,
  },
  NPC: {
    id: "NPC",
    title: "NPC / recruit approach",
    body: `Talking a character into joining or cooperating.

Four backgrounds scale Friendly / Direct / Threaten / Recruit as a [[percent]]: \`score × (100+N) / 100\`. Threaten is also mixed into interrogation. [[Leadership]] and [[Wisdom]] feed the underlying scores.`,
  },
  Experience: {
    id: "Experience",
    title: "Experience level",
    body: `How seasoned the merc is, clamped **1–10**.

10× term in this turn's [[AP]] formula. Ambush radius starts at 10× level. NCTH and many skill checks add it. The "levels faster underground" flag is a [[flat]] +1 while below ground, not a [[percent]].

Drunk and some phobias cut effective level.`,
  },
};
