import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";

import { useUnsavedGuard } from "../lib/useUnsavedGuard";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  compilePortrait,
  formatApiError,
  getApiBaseUrl,
  getSlot,
  updateMercStreaming,
  type SaveProgressEvent,
} from "../lib/api";
import { getServerToken } from "../lib/tauri";
import AnimationFrameStrip from "../components/AnimationFrameStrip";
import BackgroundPicker from "../components/BackgroundPicker";
import EyeMouthPicker, { type SubframeBox } from "../components/EyeMouthPicker";
import FaceGearCapacityBanner from "../components/FaceGearCapacityBanner";
import FaceGearOverlayAuthor from "../components/FaceGearOverlayAuthor";
import PortraitDropzone from "../components/PortraitDropzone";
import SaveProgressBar from "../components/SaveProgressBar";
import SaveSnapshotBanner from "../components/SaveSnapshotBanner";
import TraitPicker from "../components/TraitPicker";
import VoiceFileManager from "../components/VoiceFileManager";
import AimEconomicsForm from "../components/forms/AimEconomicsForm";
import AppearancePaletteForm from "../components/forms/AppearancePaletteForm";
import DemographicsForm from "../components/forms/DemographicsForm";
import GrowthModifiersForm from "../components/forms/GrowthModifiersForm";
import type { AimBinding, Merc } from "../lib/schema";
import { ATTITUDE_OPTIONS } from "../lib/attitudes";
import { CHARACTER_TRAIT_OPTIONS } from "../lib/characterTraits";
import { DISABILITY_OPTIONS } from "../lib/disabilities";

const TYPE_OPTIONS: ReadonlyArray<readonly [number, string]> = [
  [1, "AIM"],
  [2, "MERC"],
  [3, "RPC"],
  [4, "NPC"],
];


// Parse the raw XML-string profile dict from /roster/{slot} into a typed Merc.
// Missing fields stay at the existing defaults so we never lose data on save.
function parseProfileToMerc(profile: Record<string, string>, fallback: Merc): Merc {
  const out: Record<string, unknown> = { ...fallback };
  const stringFields = new Set([
    "zName", "zNickname", "PANTS", "VEST", "SKIN", "HAIR",
    "biographyText", "additionalInfoText",
  ]);
  for (const [key, raw] of Object.entries(profile)) {
    if (stringFields.has(key)) {
      out[key] = raw;
    } else {
      const n = parseInt(raw, 10);
      if (!Number.isNaN(n)) out[key] = n;
    }
  }
  return out as unknown as Merc;
}

function blankMerc(slot: number): Merc {
  // Minimal skeleton so parseProfileToMerc has every field as a default; the
  // real XML almost always overwrites all of these.
  return {
    uiIndex: slot, ubFaceIndex: slot, Type: 1,
    zName: "", zNickname: "", bSex: 0, ubBodyType: 0, uiBodyTypeSubFlags: 0,
    usVoiceIndex: slot, bRace: 0, bNationality: 0,
    usEyesX: 10, usEyesY: 8, usMouthX: 7, usMouthY: 28,
    uiEyeDelay: 0, uiMouthDelay: 0, uiBlinkFrequency: 3000, uiExpressionFrequency: 2000,
    PANTS: "BROWNPANTS", VEST: "BROWNVEST", SKIN: "PINKSKIN", HAIR: "BROWNHEAD",
    bLifeMax: 80, bLife: 80, bStrength: 70, bAgility: 70, bDexterity: 70, bWisdom: 70,
    bExpLevel: 3, bEvolution: 0,
    bMarksmanship: 70, bExplosive: 20, bLeadership: 30, bMedical: 15, bMechanical: 20,
    fRegresses: 0,
    GrowthModifierLife: 0,
    GrowthModifierStrength: 0, GrowthModifierAgility: 0,
    GrowthModifierDexterity: 0, GrowthModifierWisdom: 0,
    GrowthModifierMarksmanship: 0, GrowthModifierExplosive: 0,
    GrowthModifierLeadership: 0, GrowthModifierMedical: 0,
    GrowthModifierMechanical: 0, GrowthModifierExpLevel: 0,
    bOldSkillTrait: 0, bOldSkillTrait2: 0,
    bNewSkillTrait1: 0, bNewSkillTrait2: 0, bNewSkillTrait3: 0, bNewSkillTrait4: 0,
    bNewSkillTrait5: 0, bNewSkillTrait6: 0, bNewSkillTrait7: 0, bNewSkillTrait8: 0,
    bNewSkillTrait9: 0, bNewSkillTrait10: 0, bNewSkillTrait11: 0, bNewSkillTrait12: 0,
    bNewSkillTrait13: 0, bNewSkillTrait14: 0, bNewSkillTrait15: 0, bNewSkillTrait16: 0,
    bNewSkillTrait17: 0, bNewSkillTrait18: 0, bNewSkillTrait19: 0, bNewSkillTrait20: 0,
    bNewSkillTrait21: 0, bNewSkillTrait22: 0, bNewSkillTrait23: 0, bNewSkillTrait24: 0,
    bNewSkillTrait25: 0, bNewSkillTrait26: 0, bNewSkillTrait27: 0, bNewSkillTrait28: 0,
    bNewSkillTrait29: 0, bNewSkillTrait30: 0,
    usBackground: 0,
    bAttitude: 0, bCharacterTrait: 0, bDisability: 0,
    ubNeedForSleep: 8, bReputationTolerance: 50, bDeathRate: 50,
    bAppearance: 0, bAppearanceCareLevel: 0,
    bRefinement: 0, bRefinementCareLevel: 0,
    bHatedNationality: -1, bHatedNationalityCareLevel: 0,
    bRacist: 0, bSexist: 0, fGoodGuy: 0,
    bBuddy1: 255, bBuddy2: 255, bBuddy3: 255, bBuddy4: 255, bBuddy5: 255,
    bHated1: 255, bHatedTime1: 0,
    bHated2: 255, bHatedTime2: 0,
    bHated3: 255, bHatedTime3: 0,
    bHated4: 255, bHatedTime4: 0,
    bHated5: 255, bHatedTime5: 0,
    bLearnToLike: 255, bLearnToLikeTime: 0,
    bLearnToHate: 255, bLearnToHateTime: 0,
    sSalary: 1000, uiWeeklySalary: 6000, uiBiWeeklySalary: 11000,
    bMedicalDeposit: 0, sMedicalDepositAmount: 0, usOptionalGearCost: 1000,
    bArmourAttractiveness: 20, bMainGunAttractiveness: 20,
    usApproachFactorFriendly: 100, usApproachFactorDirect: 100,
    usApproachFactorThreaten: 100, usApproachFactorRecruit: 100,
    sSectorX: 0, sSectorY: 0, sSectorZ: 0,
    ubCivilianGroup: 0, bTown: 0, bTownAttachment: 0,
    biographyText: "", additionalInfoText: "",
  };
}

export default function Edit() {
  const [params, setParams] = useSearchParams();
  const slotParam = params.get("slot");
  const slot = slotParam ? Number(slotParam) : null;

  // No slot in the URL → bounce to the new Merc Wizard roster grid
  // instead of the local "old style" picker. The roster's per-slot
  // click handler navigates back to /edit?slot=N, completing the
  // loop. User feedback: "the pick a different merc option takes
  // you to the old roster rather than the new one — why does the
  // old roster style even exist?" Answer: EditPicker was the V1
  // simple-cards picker; it's redundant now that MercWizardRoster
  // exists with category filters, search, and proper slot tiers.
  if (slot === null || Number.isNaN(slot)) {
    return <Navigate to="/merc-wizard" replace />;
  }
  return <EditForm key={slot} slot={slot} onBack={() => setParams({})} />;
}

// EditPicker (the V1 simple-card "pick a merc" grid) was deleted
// 2026-05-25. The /edit route now redirects to /merc-wizard when
// there's no ?slot=N param. See the Edit() function above.

// Animated loading state for the Edit page. The static "Loading
// merc at slot N..." text gave the user nothing to indicate the page
// was still alive during long getSlot() calls (heavily-modded
// installs take 1-3s to parse MercProfiles.xml). A user
// hit this on slot 196 ("REALLY long time", "no loading bar").
//
// Pattern: indeterminate animated bar + an elapsed-seconds counter
// that turns the messaging from "wait" into "slow on heavily-modded
// installs — Wasteland's MercProfiles.xml is ~2 MB" once it crosses
// 2 s. The user knows what's happening and why.
function EditLoadingState({ slot }: { slot: number }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const handle = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(handle);
  }, []);
  const slow = seconds >= 2;
  return (
    <div className="mx-auto max-w-2xl px-6 py-12">
      <div className="card space-y-3">
        <div className="flex items-center gap-3">
          <span className="inline-block w-5 h-5 rounded-full border-2 border-wasteland-700 border-t-rust-400 animate-spin" />
          <span className="text-sm text-wasteland-100">
            Loading merc at slot {slot}{seconds > 0 ? ` (${seconds}s)` : "…"}
          </span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full border border-wasteland-800 bg-wasteland-900">
          <div className="h-full w-full bg-gradient-to-r from-rust-700/30 via-rust-500/60 to-rust-700/30 animate-pulse" />
        </div>
        <p className="text-xs text-wasteland-500">
          {slow ? (
            <>
              Heavily-modded installs (Wasteland, AIMNAS, etc.) have
              a ~2 MB <code className="font-mono">MercProfiles.xml</code>{" "}
              that takes a couple seconds to parse. The first slot you
              open after launch is the slow one; subsequent slots reuse
              the cached parse.
            </>
          ) : (
            <>
              Reading <code className="font-mono">MercProfiles.xml</code>,{" "}
              <code className="font-mono">AIMAvailability.xml</code>, and{" "}
              <code className="font-mono">MercStartingGear.xml</code>…
            </>
          )}
        </p>
      </div>
    </div>
  );
}

function EditForm({ slot, onBack }: { slot: number; onBack: () => void }) {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  // "Pick a different merc" sends the user to the Merc Wizard
  // roster grid (the new picker with category filters + search) rather
  // than the V1 simple-cards EditPicker.
  // onBack kept around for the error-state button below; it clears
  // the slot URL which causes Edit() to redirect to /merc-wizard.
  void onBack;
  const slotData = useQuery({
    queryKey: ["slot", slot],
    queryFn: () => getSlot(slot),
  });

  const initialMerc = useMemo(() => {
    if (!slotData.data) return null;
    return parseProfileToMerc(slotData.data.profile, blankMerc(slot));
  }, [slotData.data, slot]);

  const [merc, setMerc] = useState<Merc | null>(null);
  const [aim, setAim] = useState<AimBinding | null>(null);

  useEffect(() => {
    if (initialMerc) setMerc(initialMerc);
  }, [initialMerc]);
  useEffect(() => {
    if (slotData.data) setAim(slotData.data.aim_binding);
  }, [slotData.data]);

  // Dirty-tracking + nav guard. JSON.stringify deep-equality is the
  // pragmatic fast path — the Merc model has no functions or non-
  // serializable fields.
  const dirty = useMemo(() => {
    if (!initialMerc || !merc) return false;
    return JSON.stringify(merc) !== JSON.stringify(initialMerc);
  }, [merc, initialMerc]);
  const { confirmNavigate } = useUnsavedGuard(dirty);
  const pickAnother = () => confirmNavigate("/merc-wizard");

  // Progress state for the streaming save. Accumulates SaveProgressEvent
  // objects as they arrive from the NDJSON stream; SaveProgressBar reads
  // this list to render its step-by-step receipt. Cleared 2 seconds after
  // a successful save (so "Saved." has time to read).
  const [saveEvents, setSaveEvents] = useState<SaveProgressEvent[] | null>(null);
  const [saveDone, setSaveDone] = useState(false);
  // Track the success-fade timeout so we can cancel it on unmount.
  // Bug-review #113 — without this the setState after the fade fires
  // on an unmounted component when the user navigates away.
  const fadeTimeoutRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (fadeTimeoutRef.current !== null) {
        window.clearTimeout(fadeTimeoutRef.current);
        fadeTimeoutRef.current = null;
      }
    };
  }, []);

  const save = useMutation({
    mutationFn: async () => {
      if (!merc) throw new Error("Merc not loaded");
      // For Type=1 (AIM) mercs, preserve the existing AIM binding if any —
      // we already have the canonical `AimBioID` from the install. If the
      // merc is being toggled INTO Type=1 from another type and there's no
      // existing binding, send `aim_binding: undefined` and the server-side
      // route will compute the canonical AimBioID via
      // `aim_availability.compute_aim_bio_id` (see bug-sweep #46).
      const aimToWrite: AimBinding | undefined =
        merc.Type === 1 && aim
          ? {
              uiIndex: merc.uiIndex,
              description: aim.description || merc.zName || merc.zNickname,
              ProfilId: aim.ProfilId,
              AimBioID: aim.AimBioID,
            }
          : undefined;
      if (fadeTimeoutRef.current !== null) {
        window.clearTimeout(fadeTimeoutRef.current);
        fadeTimeoutRef.current = null;
      }
      setSaveEvents([]);
      setSaveDone(false);
      return updateMercStreaming(
        slot,
        { merc, aim_binding: aimToWrite },
        undefined,
        (ev) => {
          setSaveEvents((prev) => (prev ? [...prev, ev] : [ev]));
        },
      );
    },
    onSuccess: () => {
      setSaveDone(true);
      qc.invalidateQueries({ queryKey: ["roster"] });
      qc.invalidateQueries({ queryKey: ["slot", slot] });
      // Edit can change usVoiceIndex — invalidate so VoiceFileManager
      // shows the new folder badge if the user changed which voice the
      // merc points at.
      qc.invalidateQueries({ queryKey: ["voice", slot] });
      // Slot picker — Edit can change merc Type, Name, ubFaceIndex,
      // any of which the picker surfaces in its tooltip/category
      // chips. Bug-review finding E4.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
      // Fade the progress bar out after 2 seconds so the user gets a
      // beat to read "Saved." before it clears.
      fadeTimeoutRef.current = window.setTimeout(() => {
        setSaveEvents(null);
        setSaveDone(false);
        fadeTimeoutRef.current = null;
      }, 2000);
    },
    onError: () => {
      // Keep the progress bar visible on error so the user can see which
      // step failed and whether rollback succeeded.
      setSaveDone(true);
    },
  });

  if (slotData.isLoading || !merc) {
    return (
      <EditLoadingState slot={slot} />
    );
  }
  if (slotData.isError) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8 space-y-3">
        <button className="btn-ghost text-sm" onClick={pickAnother}>← Pick a different merc</button>
        <div className="card text-rust-400">Couldn't load slot {slot}. {formatApiError(slotData.error)}</div>
      </div>
    );
  }

  const set = <K extends keyof Merc>(key: K, value: Merc[K]) => setMerc({ ...merc, [key]: value });
  const setNum = <K extends keyof Merc>(key: K) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    set(key, Number(e.target.value) as Merc[K]);

  const tab = ((params.get("tab") as EditTab | null) ?? "profile") as EditTab;
  const setTab = (t: EditTab) => setParams({ slot: String(slot), tab: t });

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">
          Edit {merc.zNickname || merc.zName || `slot ${slot}`}{" "}
          <span className="text-wasteland-400 text-sm font-normal">(slot {slot})</span>
        </h1>
        <div className="flex gap-2">
          <button className="btn-ghost text-sm" onClick={pickAnother}>Pick a different merc</button>
          <button
            type="button"
            className="btn-ghost text-sm"
            onClick={() => confirmNavigate("/")}
          >
            ← Hub
          </button>
        </div>
      </div>

      {/* Save-snapshot warning: surfaces when this merc appears in any
          existing .SAV file. Engine snapshots stats into SOLDIERTYPE at
          hire-time, so post-hire edits don't retroactively update the
          save. Hidden when the slot has no save references (the common
          case for freshly-created mercs). See SaveSnapshotBanner. */}
      <SaveSnapshotBanner slot={slot} action="edit" />

      <nav className="flex gap-1 border-b border-wasteland-700">
        {(
          [
            ["profile", "Profile"],
            ["portrait", "Portrait"],
            ["voice", "Voice"],
            ["facegear", "FaceGear"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={`px-3 py-1.5 text-sm border-b-2 -mb-px transition-colors ${
              tab === id
                ? "border-rust-500 text-rust-200"
                : "border-transparent text-wasteland-400 hover:text-wasteland-200"
            }`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "portrait" && (
        <EditPortraitTab merc={merc} />
      )}
      {tab === "voice" && (
        <section className="card">
          <h2 className="text-sm font-semibold uppercase text-wasteland-400 mb-3">
            Voice clips · Speech/{merc.usVoiceIndex}/
          </h2>
          <p className="text-xs text-wasteland-400 mb-3">
            Voice donor:{" "}
            <code className="font-mono text-rust-400">usVoiceIndex={merc.usVoiceIndex}</code>.
            Change on the Profile tab if you want a different donor folder.
          </p>
          <VoiceFileManager slot={slot} />
        </section>
      )}
      {tab === "facegear" && (
        <section className="card">
          <h2 className="text-sm font-semibold uppercase text-wasteland-400 mb-3">
            FaceGear capacity
          </h2>
          <p className="text-xs text-wasteland-400 mb-3">
            Each <code className="font-mono">Face_*.sti</code> needs at least{" "}
            <code className="font-mono">ubFaceIndex + 1 = {merc.ubFaceIndex + 1}</code> frames
            for this merc to safely equip the corresponding gear in-game.
          </p>
          <FaceGearCapacityBanner faceIndex={merc.ubFaceIndex} />
          <FaceGearOverlayAuthor
            faceIndex={merc.ubFaceIndex}
            eyeX={merc.usEyesX}
            eyeY={merc.usEyesY}
          />
        </section>
      )}

      {tab === "profile" && (
        <>
      <section className="card grid grid-cols-2 gap-4">
        <label className="block col-span-2">
          <span className="text-sm font-medium text-wasteland-100">Full name</span>
          <input
            className="input mt-1"
            value={merc.zName}
            maxLength={50}
            onChange={(e) => set("zName", e.target.value)}
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-wasteland-100">Nickname (max 9)</span>
          <input
            className="input mt-1"
            value={merc.zNickname}
            maxLength={9}
            onChange={(e) => set("zNickname", e.target.value)}
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-wasteland-100">Type</span>
          <select className="input mt-1" value={merc.Type} onChange={(e) => set("Type", Number(e.target.value) as Merc["Type"])}>
            {TYPE_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          {initialMerc && initialMerc.Type === 1 && merc.Type !== 1 && (
            <div className="mt-2 rounded border border-yellow-500/40 bg-yellow-500/10 p-2 text-xs text-yellow-200">
              Changing Type from AIM to {merc.Type === 2 ? "MERC" : merc.Type === 3 ? "RPC" : `Type ${merc.Type}`} will drop the AIM-website binding. The merc stays in MercProfiles but won't appear on the AIM hire page anymore. Switch back to AIM before saving if you didn't mean this.
            </div>
          )}
          {initialMerc && initialMerc.Type !== 1 && merc.Type === 1 && (
            <div className="mt-2 rounded border border-rust-500/40 bg-rust-500/10 p-2 text-xs text-rust-200">
              Changing Type to AIM. The server will compute a fresh AimBioID from AIMAvailability.xml. You won't see this merc on the AIM hire page until the install actually has an AIM seat free.
            </div>
          )}
        </label>
        <label className="block">
          <span className="text-sm font-medium text-wasteland-100">Sex</span>
          <select className="input mt-1" value={merc.bSex} onChange={(e) => set("bSex", Number(e.target.value) as 0 | 1)}>
            <option value={0}>Male</option>
            <option value={1}>Female</option>
          </select>
        </label>
        <label className="block">
          <span className="text-sm font-medium text-wasteland-100">Voice index</span>
          <input
            type="number"
            className="input mt-1"
            value={merc.usVoiceIndex}
            min={0}
            max={255}
            onChange={setNum("usVoiceIndex")}
          />
        </label>
      </section>

      <section className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase text-wasteland-400">Attributes</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {([
            ["bLifeMax", "Max HP"],
            ["bStrength", "Strength"],
            ["bAgility", "Agility"],
            ["bDexterity", "Dexterity"],
            ["bWisdom", "Wisdom"],
            ["bMarksmanship", "Marksmanship"],
            ["bExplosive", "Explosive"],
            ["bLeadership", "Leadership"],
            ["bMedical", "Medical"],
            ["bMechanical", "Mechanical"],
            ["bExpLevel", "Exp level"],
          ] as const).map(([k, label]) => (
            <label key={k} className="block">
              <span className="text-xs text-wasteland-300">{label}</span>
              <input
                type="number"
                className="input mt-1"
                value={merc[k] as number}
                min={k === "bExpLevel" ? 1 : 0}
                max={k === "bExpLevel" ? 10 : 100}
                onChange={setNum(k)}
              />
            </label>
          ))}
        </div>
      </section>

      <section className="card grid grid-cols-3 gap-3">
        <label className="block">
          <span className="text-sm font-medium text-wasteland-100">Attitude</span>
          <select className="input mt-1" value={merc.bAttitude} onChange={setNum("bAttitude")}>
            {ATTITUDE_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-sm font-medium text-wasteland-100">Character trait</span>
          <select className="input mt-1" value={merc.bCharacterTrait} onChange={setNum("bCharacterTrait")}>
            {CHARACTER_TRAIT_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-sm font-medium text-wasteland-100">Disability</span>
          <select className="input mt-1" value={merc.bDisability} onChange={setNum("bDisability")}>
            {DISABILITY_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
      </section>

      <section className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase text-wasteland-400">Biography</h2>
        <label className="block">
          <span className="text-xs text-wasteland-300">Main biography (max 400 chars — {400 - merc.biographyText.length} left)</span>
          <textarea
            className="input mt-1"
            rows={6}
            maxLength={400}
            value={merc.biographyText}
            onChange={(e) => set("biographyText", e.target.value)}
          />
        </label>
        <label className="block">
          <span className="text-xs text-wasteland-300">Additional info (max 160 chars — {160 - merc.additionalInfoText.length} left)</span>
          <textarea
            className="input mt-1"
            rows={3}
            maxLength={160}
            value={merc.additionalInfoText}
            onChange={(e) => set("additionalInfoText", e.target.value)}
          />
        </label>
      </section>

      {/* ── Demographics: race, nationality, body type ────────────────
          Was missing from Edit pre-2026-05-24 (user feedback: "edit seems to be
          missing a bunch of stuff you could edit"). Shared component
          with Create.tsx via components/forms/. */}
      <section className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase text-wasteland-400">Demographics</h2>
        <DemographicsForm merc={merc} onChange={set} />
      </section>

      {/* ── Sprite palette (tactical-view colors) ─────────────────────
          PANTS / VEST / SKIN / HAIR. Engine tints the tactical sprite
          (NOT the portrait — portrait colors are baked into the STI). */}
      <section className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase text-wasteland-400">Appearance</h2>
        <AppearancePaletteForm merc={merc} onChange={set} />
      </section>

      {/* ── Skill Traits (NT / OT depending on install) ───────────────
          Uses the existing TraitPicker which auto-detects the trait
          system from the active install's catalog. NT (1.13 STOMP) lands
          picks in bNewSkillTrait1..N; OT (vanilla) lands picks in
          bOldSkillTrait + bOldSkillTrait2. */}
      <section className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase text-wasteland-400">Skill Traits</h2>
        <TraitPicker merc={merc} onChange={setMerc} />
      </section>

      {/* ── Background (spawn story / role) ───────────────────────────
          usBackground = entry ID in the install's Backgrounds.xml.
          0 = no background. Affects starting items, sector spawn,
          and AIM bio rendering. */}
      <section className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase text-wasteland-400">Background</h2>
        <BackgroundPicker
          value={merc.usBackground}
          onChange={(id) => set("usBackground", id)}
        />
      </section>

      {/* ── Growth Modifiers (collapsed by default) ───────────────────
          Per-stat skill-gain percentage. Most vanilla mercs are all
          zero; mods use small positives to specialize. */}
      <section className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase text-wasteland-400">Growth Modifiers</h2>
        <GrowthModifiersForm merc={merc} onChange={set} />
      </section>

      {/* ── AIM Economics (salary, contract tiers, medical deposit) ──
          Only AIM mercs (Type=1) use these in-engine, but values
          round-trip preserved on any Type. */}
      <section className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase text-wasteland-400">AIM Economics</h2>
        <AimEconomicsForm merc={merc} onChange={set} />
      </section>

      <section className="card space-y-3">
        <div className="flex items-center justify-between">
          <button
            className="btn-primary"
            disabled={save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving..." : "Save changes"}
          </button>
          {save.isSuccess && !saveEvents && (
            <span className="text-sm text-rust-400">Saved.</span>
          )}
        </div>
        <SaveProgressBar
          events={saveEvents}
          done={saveDone}
          error={save.isError ? save.error : null}
        />
        {save.isError && !saveEvents && (
          <div className="rounded border border-rust-500/40 bg-rust-500/10 p-3 text-sm text-rust-200">
            Save failed: {formatApiError(save.error)}
          </div>
        )}
        <p className="text-xs text-wasteland-400">
          Portrait + voice live on the Portrait / Voice tabs above. Gear edits aren't exposed yet
          — re-Create to change a merc's starting kit.
        </p>
      </section>
        </>
      )}
    </div>
  );
}

type EditTab = "profile" | "portrait" | "voice" | "facegear";

/** Skeleton block sized to the bigface portrait (106×122). Used both
 * pre-URL (while the api base + token resolve) and while the IMG is
 * fetching. Pulsing animation makes "is it loading or stuck?" visually
 * obvious. User feedback: "no loading bar or clear it's loading
 * anything on the portrait and voice pages it takes a while to load." */
function PortraitSkeleton({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-busy="true"
      className="w-full h-full rounded border border-wasteland-800 bg-wasteland-900 flex flex-col items-center justify-center gap-1.5 animate-pulse"
    >
      <span className="inline-block h-3 w-3 rounded-full border-2 border-rust-500 border-t-transparent animate-spin" />
      <span className="text-[9px] text-wasteland-500">{label}</span>
    </div>
  );
}

/** Image with explicit loading + error states. Renders the
 * PortraitSkeleton while the IMG is fetching, hides on 404/204 (slot
 * has no portrait on disk yet), shows the painting on success. */
function PortraitImage({ url, alt }: { url: string; alt: string }) {
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  // Reset to loading whenever the URL changes (e.g. after a recompile
  // bumps the cache-busting suffix).
  useEffect(() => { setState("loading"); }, [url]);
  if (state === "error") {
    return (
      <div className="mx-auto" style={{ width: 106, height: 122 }}>
        <div className="w-full h-full rounded border border-wasteland-800 bg-wasteland-950 flex items-center justify-center text-[10px] text-wasteland-500 text-center px-2">
          No portrait on disk yet — pick one below to write it.
        </div>
      </div>
    );
  }
  return (
    <div className="relative mx-auto" style={{ width: 106, height: 122 }}>
      {state === "loading" && (
        <div className="absolute inset-0">
          <PortraitSkeleton label="Loading portrait…" />
        </div>
      )}
      <img
        src={url}
        alt={alt}
        className="block"
        style={{
          width: 106,
          height: 122,
          imageRendering: "pixelated",
          objectFit: "contain",
          opacity: state === "loading" ? 0 : 1,
        }}
        onLoad={() => setState("ok")}
        onError={() => setState("error")}
      />
    </div>
  );
}

function EditPortraitTab({ merc }: { merc: Merc }) {
  const qc = useQueryClient();
  const [portrait, setPortrait] = useState<File | null>(null);
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null);
  const [bigfaceImage, setBigfaceImage] = useState<File | null>(null);
  const [eyeFrames, setEyeFrames] = useState<(File | null)[]>([null, null, null, null]);
  const [mouthFrames, setMouthFrames] = useState<(File | null)[]>([null, null, null]);
  const [eyeBox, setEyeBox] = useState<SubframeBox>({
    x: merc.usEyesX, y: merc.usEyesY, w: 17, h: 6,
  });
  const [mouthBox, setMouthBox] = useState<SubframeBox>({
    x: merc.usMouthX, y: merc.usMouthY, w: 14, h: 6,
  });
  // Live URL of the merc's CURRENT portrait on disk, for the "Current
  // portrait" preview row above the dropzone. Without this the user
  // arrived at the Portrait tab and saw an empty dropzone — no signal
  // that the merc already had art — which made it hard to tell what
  // they were about to overwrite.
  //
  // Built with a `?_t=<token>` query param because `<img>` tags can't
  // attach the X-MercWizard-Token header. See mediaUrl() docstring.
  const [currentPortraitUrl, setCurrentPortraitUrl] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    Promise.all([getApiBaseUrl(), getServerToken()]).then(([base, token]) => {
      if (cancelled) return;
      const tokenQs = token ? `&_t=${encodeURIComponent(token)}` : "";
      // Cache-bust on every mount so a fresh recompile shows new art
      // without waiting on the 60s server Cache-Control. The roster
      // query invalidation also bumps the URL, but the Edit tab opens
      // before that fires when navigating from the roster row.
      setCurrentPortraitUrl(
        `${base}/merc/${merc.uiIndex}/portrait?size=bigface&v=${Date.now()}${tokenQs}`,
      );
    }).catch(() => {
      // No reachable sidecar yet — leave the preview empty; the dropzone
      // still works for picking a new file.
    });
    return () => { cancelled = true; };
  }, [merc.uiIndex]);

  useEffect(() => {
    return () => {
      if (portraitUrl) URL.revokeObjectURL(portraitUrl);
    };
  }, [portraitUrl]);

  const recompile = useMutation({
    mutationFn: async () => {
      if (!portrait) throw new Error("Pick a portrait first");
      return compilePortrait(portrait, merc.ubFaceIndex, {
        eye_x: eyeBox.x, eye_y: eyeBox.y, eye_w: eyeBox.w, eye_h: eyeBox.h,
        mouth_x: mouthBox.x, mouth_y: mouthBox.y, mouth_w: mouthBox.w, mouth_h: mouthBox.h,
        skip_animation: true,
        bigface_image: bigfaceImage ?? undefined,
        anim_eye_1: eyeFrames[0] ?? undefined,
        anim_eye_2: eyeFrames[1] ?? undefined,
        anim_eye_3: eyeFrames[2] ?? undefined,
        anim_eye_4: eyeFrames[3] ?? undefined,
        anim_mouth_1: mouthFrames[0] ?? undefined,
        anim_mouth_2: mouthFrames[1] ?? undefined,
        anim_mouth_3: mouthFrames[2] ?? undefined,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["roster"] });
    },
  });

  return (
    <section className="card space-y-4">
      <h2 className="text-sm font-semibold uppercase text-wasteland-400">
        Recompile portrait · face index {merc.ubFaceIndex}
      </h2>
      <p className="text-xs text-wasteland-400">
        Writes new <code className="font-mono">BigFace</code>, <code className="font-mono">SmallFace</code>,
        <code className="font-mono">65Face</code>, and <code className="font-mono">33Face</code> STIs at
        face index <code className="font-mono">{merc.ubFaceIndex}</code> in the active install. The
        merc's <code className="font-mono">usEyesX/Y</code> + <code className="font-mono">usMouthX/Y</code>{" "}
        in MercProfiles.xml are NOT updated by this tab — save the Profile tab to change those.
      </p>

      {/* Current portrait on disk — shows what's there before the user
          uploads a replacement. Separate from the dropzone's preview
          so picking "Write portrait" never accidentally writes the
          existing art back over itself. Falls back to a quiet
          placeholder when the merc has no portrait or the sidecar
          isn't reachable. */}
      <div className="rounded border border-wasteland-700 bg-wasteland-950/40 p-3">
        <div className="text-xs uppercase text-wasteland-500 mb-2">
          Current portrait on disk · face {merc.ubFaceIndex}
        </div>
        {currentPortraitUrl ? (
          <PortraitImage
            url={currentPortraitUrl}
            alt={`Current portrait for ${merc.zNickname || merc.zName || `slot ${merc.uiIndex}`}`}
          />
        ) : (
          /* Token/api-base not resolved yet (very early in tab open).
              Render the same skeleton shape so the layout doesn't jump
              when the IMG finally appears. */
          <div className="mx-auto" style={{ width: 106, height: 122 }}>
            <PortraitSkeleton label="Resolving portrait URL…" />
          </div>
        )}
      </div>

      <PortraitDropzone
        previewUrl={portraitUrl}
        onFileSelected={(f) => {
          setPortrait(f);
          if (portraitUrl) URL.revokeObjectURL(portraitUrl);
          setPortraitUrl(URL.createObjectURL(f));
        }}
        onClear={() => {
          if (portraitUrl) URL.revokeObjectURL(portraitUrl);
          setPortrait(null);
          setPortraitUrl(null);
        }}
        className="aspect-square max-w-md mx-auto"
      />

      <div>
        <h3 className="text-xs font-semibold text-wasteland-200 mb-2">Eye &amp; mouth regions</h3>
        <EyeMouthPicker
          portrait={portrait}
          eyeBox={eyeBox}
          mouthBox={mouthBox}
          onEyeBoxChange={setEyeBox}
          onMouthBoxChange={setMouthBox}
        />
      </div>

      <details className="rounded border border-wasteland-700">
        <summary className="cursor-pointer px-3 py-2 text-sm text-wasteland-200">
          Advanced: animation frames + separate BigFace (optional)
        </summary>
        <div className="p-3 space-y-3">
          <div>
            <span className="text-xs text-wasteland-300">BigFace source (106×122)</span>
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp,image/bmp"
              onChange={(e) => setBigfaceImage(e.target.files?.[0] ?? null)}
              className="block mt-1 text-xs"
            />
          </div>
          <AnimationFrameStrip
            eyeFrames={eyeFrames}
            mouthFrames={mouthFrames}
            onEyeFramesChange={setEyeFrames}
            onMouthFramesChange={setMouthFrames}
          />
        </div>
      </details>

      <div className="flex items-center gap-3 pt-2">
        <button
          type="button"
          className="btn-primary"
          disabled={!portrait || recompile.isPending}
          onClick={() => recompile.mutate()}
        >
          {recompile.isPending ? "Recompiling…" : "Recompile portrait"}
        </button>
        {recompile.isSuccess && (
          <span className="text-sm text-green-400">
            ✓ Wrote {recompile.data?.files_written.length ?? 0} STI files.
          </span>
        )}
        {recompile.isError && (
          <span className="text-sm text-rust-400">
            Recompile failed: {formatApiError(recompile.error)}
          </span>
        )}
      </div>
    </section>
  );
}
