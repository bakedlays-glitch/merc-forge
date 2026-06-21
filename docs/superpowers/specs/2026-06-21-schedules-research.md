# JA2 1.13 NPC SCHEDULES Appendix — Read-Only Parse Spec

**Scope:** the last appendix section (`SCHEDULES`), gated by `MAP_NPCSCHEDULES_SAVED = 0x00000100`.
Appendix order: `… → exit grids → door table → edgepoints → SCHEDULES → (editor trailing data) → EOF`.
**Status: READ-ONLY research. No code was written or modified.** (A temp probe script was created in a
throwaway `scratch_sched/` dir, run, and deleted; nothing in `sidecar/`, source, or tests was touched.)

---

## 0. Bottom line up front

- **Count width = `UINT8` (1 byte).** Source: `SaveSchedules`/`LoadSchedules` write/read `ubNum` as
  `UINT8` (`Scheduling.cpp:605, 560`). Max written = 32 (`Scheduling.cpp:605`, clamp `ubNum = iNum>=32 ? 32 : iNum`).
- **Per-record size is VERSION-DEPENDENT and selected by the map's `dMajorMapVersion`** (read from the
  file header, the same `parsed["major"]` the appendix extractor already uses):
  - `major < 7.0`  → **`_OLD_SCHEDULENODE` = 36 bytes** (UINT16 usData). **← all shipped JA2 maps.**
  - `7.0 ≤ major < 8.0` → `_OLD_SCHEDULENODE_PRE_ITS` = 52 bytes (UINT32 usData, UINT8 soldierID).
  - `major ≥ 8.0` → modern `SCHEDULENODE` = 56 bytes (UINT32 usData, `SoldierID{UINT16}`).
- **Every real `.DAT` map in both installs is `major = 5.0`** → the **36-byte old record** is the one that
  matters in practice. Empirically validated on all maps that reach the section (see §3). The 52/56-byte
  layouts produce garbage on these maps and are documented only for completeness / forward-compat.

---

## 1. Record format + field offsets

### 1a. Section framing (`LoadSchedules`, `Scheduling.cpp:551-591`)
```
SCHEDULES := ubNum (UINT8) , ubNum × <record>
```
There is **no trailing terminator**. After the records, the file may contain an **editor-only trailing
region** the engine never reads (variable size — observed 0…37,474 bytes, see §3). The records themselves
parse to an exact, consistent boundary.

### 1b. The 36-byte old record — `_OLD_SCHEDULENODE` (Scheduling.h:45-55)
`MAX_SCHEDULE_ACTIONS = OLD_MAX_SCHEDULE_ACTIONS = 4` (Scheduling.h:42-43).
Loaded whole via `LOADDATA(&OldScheduleNode, *hBuffer, sizeof(_OLD_SCHEDULENODE))`
(`SCHEDULENODE::Load`, `Scheduling.cpp:509-513`, the `dMajorMapVersion < 7.0` branch).

MSVC x86-32 default packing (BOOLEAN/UINT8=1, UINT16=2, ptr=4, struct align = 4 = largest member). The
`next` pointer **is part of `sizeof` and IS written to disk** (the whole POD incl. the stale pointer is
`memcpy`/`LOADDATA`-ed; the engine then overwrites `next` and `ubSoldierID` after load):

| offset | field            | type        | size | note                                            |
|-------:|------------------|-------------|-----:|-------------------------------------------------|
| 0      | `next`           | ptr         | 4    | stale on-disk pointer; engine ignores/overwrites |
| 4      | `usTime[4]`      | UINT16×4    | 8    | minutes-in-day; `0xFFFF` = unused slot           |
| 12     | `usData1[4]`     | **UINT16×4**| 8    | **primary gridno** for move/door/sleep actions   |
| 20     | `usData2[4]`     | **UINT16×4**| 8    | secondary gridno (door "move-to-after")          |
| 28     | `ubAction[4]`    | UINT8×4     | 4    | action code per slot (see §2)                    |
| 32     | `ubScheduleID`   | UINT8       | 1    | **the soldier link** (see §1d)                   |
| 33     | `ubSoldierID`    | UINT8       | 1    | **STALE on disk — do NOT use** (see §1d)          |
| 34     | `usFlags`        | UINT16      | 2    | `SCHEDULE_FLAGS_*` (Scheduling.h:26-39)           |
| —      | —                | —           | —    | total 36, already 4-aligned (no tail pad)        |

Derivation checks out: `4+8+8+8+4+1+1+2 = 36`, divisible by 4, no internal padding (every field already
lands on its natural alignment). Confirmed by a Python re-derivation of the offsets.

### 1c. The other two layouts (NOT used by shipped maps — for completeness)
- `_OLD_SCHEDULENODE_PRE_ITS` (Scheduling.h:57-67), `7.0 ≤ major < 8.0`: same shape but `usData1/2` are
  **UINT32×4** (16B each) and `ubSoldierID` is plain UINT8 → `4+8+16+16+4+1+1+2 = 52`, 4-aligned → **52 B**.
  usData1 @ off 12, usData2 @ 28, ubAction @ 44, schedID @ 48, soldierID @ 49, flags @ 50.
- modern `SCHEDULENODE` (Scheduling.h:69-85), `major ≥ 8.0`: `usData` UINT32×4, and `ubSoldierID` is the
  `SoldierID` **struct** = `{ UINT16 i; }` (Overhead Types.h:382-428), align 2, size 2. Layout:
  next@0, usTime@4, usData1@12, usData2@28, ubAction@44, ubScheduleID@48, **pad@49**, ubSoldierID(u16)@50,
  usFlags@52 → 54 raw → 4-align → **56 B**.

### 1d. How a schedule links to a soldier — use `ubScheduleID`, NOT the saved `ubSoldierID`
On load, `LoadSchedules` **discards** the on-disk soldier id: it assigns `ubScheduleID = 1,2,3,…`
sequentially and sets `ubSoldierID = NOBODY` (`Scheduling.cpp:584-585`). The link is reconstructed later by
matching `schedule.ubScheduleID` against `SOLDIERINITNODE->pDetailedPlacement->ubScheduleID` /
`pSoldier->ubScheduleID` (`CopyScheduleToList` Scheduling.cpp:67-70; `OptimizeSchedules` :269-310).
**Empirically the on-disk `ubSoldierID` byte is a constant junk value (156 on every record of every map)**,
confirming it must NOT be trusted. The `ubScheduleID` (offset 32) is the real key, and it always reads as a
clean `1..ubNum` run (which is itself a strong record-boundary check).

> Plotting caveat: the soldier that owns a schedule is the basic/detailed placement whose `ubScheduleID`
> equals the schedule's. That `ubScheduleID` lives in the **detailed** `SOLDIERCREATE_STRUCT`
> (Soldier Create.h:231/348/434), i.e. inside the 1040-byte detailed block the extractor currently
> reads-but-skips. To label a waypoint with its soldier you'd extract that byte during the soldier pass.
> For a v1 overlay you can plot the bare waypoint gridnos without the soldier label.

---

## 2. Which `usData` fields are plottable gridnos

Per-action consumption (`DecideAction.cpp` `ExecuteScheduleAction`, the `usData1=usGridNo1` /
`usData2=usGridNo2` switch at lines 120-540):

| code | action        | usData1                     | usData2                       | plot? |
|-----:|---------------|-----------------------------|-------------------------------|-------|
| 0    | NONE          | unused (`0xFFFF`)           | unused                        | no    |
| 1    | LOCKDOOR      | door gridno (:130)          | move-to-after gridno          | both  |
| 2    | UNLOCKDOOR    | door gridno                 | move-to-after gridno          | both  |
| 3    | OPENDOOR      | door gridno                 | move-to-after gridno          | both  |
| 4    | CLOSEDOOR     | door gridno                 | move-to-after gridno          | both  |
| 5    | GRIDNO        | **move-to gridno** (:402)   | unused                        | **D1**|
| 6    | LEAVESECTOR   | unused (:408 "no gridno")   | unused                        | no    |
| 7    | ENTERSECTOR   | unused                      | unused                        | no    |
| 8    | STAYINSECTOR  | unused                      | unused                        | no    |
| 9    | SLEEP         | **sleep-spot gridno** (:535)| unused                        | **D1**|
| 10   | WAKE          | unused                      | unused                        | no    |

**How to tell a gridno is real:** a slot is "used" iff `ubAction != NONE(0)` AND `usData != 0xFFFF(65535)`.
The cleanest plot set = **`usData1` where `ubAction ∈ {GRIDNO, SLEEP}`** (true movement waypoints), optionally
plus door gridnos for `ubAction ∈ {LOCKDOOR..CLOSEDOOR}`. Validity bound: `0 ≤ gridno < cols*rows ≤ 25600`.

---

## 3. Empirical results

Cursor walk replicated the shipped extractor exactly through edgepoints, then read `UINT8 ubNum` +
`ubNum × 36`. Maps from both installs (`…\Mod Prototype - Copy\…\Maps` and `…\Mod Prototype\…\Maps`).

**Maps that reach the section all validate under the 36-byte record** (schedIDs always a clean `1..ubNum`
run, all move/sleep `usData1` in-bounds, lands at a consistent boundary):

| map           | major/minor | ubNum | plottable D1 gridnos | trailing-to-EOF | schedIDs |
|---------------|-------------|-------|----------------------|-----------------|----------|
| **A2** (Copy) | 5.0 / 25    | **7** | **7** (e.g. 9666, 18954, 9669, 18956, 17078) | 2147 | 1..7 |
| **C6** (Copy) | 5.0 / 25    | 10    | 9                    | 1668            | 1..10    |
| A10 (Copy)    | 5.0 / 25    | 5     | 0 (all SLEEP=17078-ish + STAY) | 498   | 1..5     |
| A1 (Copy)     | 5.0 / 25    | 1     | 1                    | 37474 (big editor tail) | 1 |
| **C5** (Base) | 5.0 / 25    | **27**| 9                    | **0 (exact EOF)**| 1..27    |
| I13 (Base)    | 5.0 / 25    | 5     | 0                    | 52              | 1..5     |
| A6/C7/A12/A15 | 5.0         | 0     | —                    | 0…2190          | (empty)  |

Sample decoded waypoints, **A2.DAT** (cols=160, num_individuals=39), all 7 records belong to one NPC
(detailed placement, schedIDs 1-7):
```
#4 schedID=5 flags=0x0000 act=[GRIDNO, SLEEP, NONE, NONE]  time=(480,1230,-,-)  d1=(9666, 18954, -, -)
#5 schedID=6 flags=0x0000 act=[GRIDNO, SLEEP, NONE, NONE]  time=(480,1230,-,-)  d1=(9669, 18956, -, -)
#0 schedID=1 flags=0x0000 act=[SLEEP, NONE,…]              d1=(17078,…)
```
`time=480` = 08:00, `1230` = 20:30 — real minute-of-day values, another correctness signal. gridno
9666 → (x=9666%160, y=9666//160) = (66, 60); 18954 → (74, 118). All < 25600. 

**Cursor lands at or before EOF on every map**; the gap is the documented editor-only trailing region
(0 on C5 — records end exactly at EOF; up to 37k on A1). A few maps (`B2/D5/J9/A10_B1` base, basement/`_B1`
variants) **bail during the pre-schedules walk** (`edge_overrun` / items bail) — a pre-existing limitation of
the extractor's edgepoint/items handling, **unrelated to the schedule record format**. `a9.dat` is
`major=7.0` with `NPCSCHED=False` (no schedules saved), consistent.

The 52- and 56-byte hypotheses were tested on A2 and produce non-sequential schedIDs, out-of-range gridnos,
and junk action codes — **conclusively ruling them out** for these (major 5.0) maps.

---

## 4. Confidence & recommendation

**Confidence: HIGH** for the format. The record size, count width, field offsets, and action→gridno mapping
are all (a) derived from source with citations, (b) consistent with MSVC padding, and (c) empirically
confirmed on 6 distinct schedule-bearing maps across both installs, including one (C5, 27 records) landing
byte-exact at EOF and another (A2) with human-meaningful times and in-bounds gridnos.

**Recommendation on shipping a schedules overlay: MARGINAL — lean SKIP for v1, or ship as a tiny
"low-effort, low-yield" extra.** Reasons:

- **The data is real but extremely sparse.** Only ~half the schedule-bearing maps have *any* plottable
  movement waypoint, and those that do yield a handful (A2: 7, C6: 9, C5: 9) — and they cluster on a single
  NPC patrolling 2-3 tiles (GRIDNO↔SLEEP between a workspot and a bed). Most slots are `SLEEP`/`STAYINSECTOR`
  with no spatial info. Across the *whole* Copy install you'd add markers to ~3 maps.
- **The soldier label needs the detailed-placement `ubScheduleID`**, which means doing the 1040-byte
  detailed-soldier extraction the overlay currently skips. Plotting *bare* gridnos (no soldier name) is cheap;
  plotting *attributed* waypoints is more work for little payoff.
- **Several maps can't even be walked to the section yet** (edgepoint/items bails), so the overlay would be
  blank on those regardless.

**If shipped, plot exactly this** (cheap, no detailed-soldier pass required):
- For each schedule record, for each of the 4 slots where `ubAction ∈ {GRIDNO(5), SLEEP(9)}` and
  `usData1 != 0xFFFF` and `usData1 < cols*rows`: a waypoint marker at `usData1`.
- Optionally door-action gridnos (`usData1` for actions 1-4) as a second marker class.
- Group markers sharing a `ubScheduleID` (same NPC's route); draw faint connectors GRIDNO→SLEEP to show
  the patrol if you want polish.
- Skip `LEAVE/ENTER/STAY/WAKE/NONE` slots and any `0xFFFF` data — they carry no position.

Net: the format is fully solved and trivial to parse (UINT8 count + N×36, fixed offsets), but the payload is
thin enough that it's a "nice-to-have garnish," not a headline overlay. An honest call is **skip unless you
want completeness**, and if you do ship it, ship the bare-gridno version (no detailed-soldier dependency).
