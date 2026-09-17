# MercWizard RPC Workflow Design

## Outcome

MercWizard can create or convert a profile into a complete recruitable RPC, build the distinct face assets an RPC needs, place it on a map tile, author an always-available or conditional recruit path, and show whether the character is ready for a new-game test.

## Product shape

The RPC is authored from the merc editor. Map placement remains a visible stage of that workflow and uses a MapForge-rendered sector picker. The first release does not write soldier records directly into map `.dat` appendices; it writes a managed `InitialProfile(...)` entry in `Scripts/GameInit.lua`, which is the version-stable engine path for profile-backed RPC insertion.

The existing profile slot remains the character's permanent identity. Sector and grid number are placement data, not a second slot number. Named engine slots retain the existing slot-lock warnings.

## Workflow

1. Choose or edit a profile slot and set `Type=3` (RPC).
2. Compile the normal four portrait assets plus the RPC-only animated `faces/B<face>.sti` talk-panel portrait.
3. Store the 90x100 talk-panel eye/mouth coordinates in `MercProfiles.xml`, while preserving the 48x43 tactical coordinates in `RPCFacesSmall.xml`.
4. Choose a sector and tile from the placement tab's MapForge image picker. MercWizard writes the placement into its marked block in `GameInit.lua`.
5. Author recruitment and dialogue. A new RPC defaults to an always-available Recruit approach; give-item and fact gates remain available as advanced branches.
6. Review a compact RPC readiness rail covering profile type, faces, placement, recruit logic, and dialogue/voice.

## Talk-face compilation

The talk-panel face is an eight-frame ETRLE STI with a 90x100 base, four eye frames, and three mouth frames. Full-face animation variants are center-cropped to 90x100 and their changed regions are detected from pixel differences. If a region has no authored variants, MercWizard emits transparent no-op frames at coordinates mapped from the 48x43 picker. This produces a structurally valid face without pretending that unauthored motion exists.

Portrait compilation remains atomic and backed up. When RPC talk-face compilation is requested, the backup set also includes `faces/B<face>.sti`. A failure restores every face file touched by that compile.

## Coordinate ownership

- `MercProfiles.xml usEyesX/Y/usMouthX/Y`: 90x100 talk-panel coordinates for RPCs.
- `RPCFacesSmall.xml`: 48x43 tactical/squad-face coordinates.
- AIM and MERC portrait compilation remains unchanged and continues to store 48x43 coordinates in the profile.

On RPC create, MercWizard uses the talk-face coordinates returned by portrait compilation when it creates the profile, then writes the small-face override. On RPC portrait replacement, it updates both coordinate stores after the face compile succeeds.

## Recruitment defaults and advanced logic

The default recruit record uses approach `4` (`NPC_ACTION_RECRUIT`) with opinion requirement `0`, making the recruit option available without a quest, item, money, or leadership gate. The accept quote defaults to custom quote slot 10. The editor warns when a branch points at a blank quote.

Advanced branches may use a nonzero opinion threshold, a required item, or a fact requirement. Existing `.NPC` files containing logic the editor cannot represent remain protected by the current lossy-overwrite guard.

## Readiness

The readiness rail is informational and never silently writes game data. It reports:

- RPC profile type and nonzero voice index;
- four normal portrait files plus a structurally valid 90x100, eight-frame talk face;
- small-face coordinate override;
- managed or hand-authored placement, with duplicate placement called out;
- at least one recruit action branch and nonblank referenced accept quotes;
- optional post-recruit dialogue/voice as an advisory item.

Each item links to the editor tab that resolves it. The overall state is "Ready for new-game test" only when all required items pass. The interface states that `InitNPCs()` placement requires a new game.

## Safety and compatibility

- All game-file writes use the existing backup, install lock, and atomic-write mechanisms.
- Hand-authored Lua outside MercWizard markers remains untouched.
- Existing unrepresentable `.NPC` logic is never overwritten without the existing explicit force control.
- Direct `.dat` soldier serialization is out of scope until the modern variable-length detailed-placement format has a complete round-trip writer.
- No engine recompile is required.

## Verification

Unit tests cover talk-face dimensions/frame layout, coordinate detection/fallback, portrait-route backup coverage, readiness classification, and the always-available default branch. Frontend tests cover readiness labels and RPC compile request wiring. The final pass runs the RPC sidecar tests, frontend tests relevant to the changed components, a production frontend bundle, and a headless sidecar/API smoke test.
