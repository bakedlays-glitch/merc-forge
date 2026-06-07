import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useUnsavedGuard } from "../lib/useUnsavedGuard";

import {
  compilePortrait,
  createMerc,
  extendFaceGear,
  getFaceGearCapacity,
  listGearPresets,
} from "../lib/api";
import AnimationFrameStrip from "../components/AnimationFrameStrip";
import AppearancePaletteForm from "../components/forms/AppearancePaletteForm";
import AuditPanel from "../components/AuditPanel";
import BackgroundPicker from "../components/BackgroundPicker";
import BiographyEditor from "../components/BiographyEditor";
import EyeMouthPicker, { type SubframeBox } from "../components/EyeMouthPicker";
import FaceGearCapacityBanner from "../components/FaceGearCapacityBanner";
import PortraitDropzone from "../components/PortraitDropzone";
import StartFromExistingPicker from "../components/StartFromExistingPicker";
import TraitPicker from "../components/TraitPicker";
import VoiceIndexHint from "../components/VoiceIndexHint";
import { salaryIsOutOfBand, suggestSalary } from "../lib/salary";
import SlotPicker from "../components/SlotPicker";
import { SlotLockWarningModal } from "../components/SlotLockWarningModal";
import StatSlider from "../components/StatSlider";
import VoiceFileManager from "../components/VoiceFileManager";
import { useSlotLockGuard } from "../lib/slotLocks";
import type { GearKit, Merc } from "../lib/schema";
import { NATIONALITY_OPTIONS } from "../lib/nationalities";
import { RACE_OPTIONS } from "../lib/races";
import { ATTITUDE_OPTIONS } from "../lib/attitudes";
import { CHARACTER_TRAIT_OPTIONS } from "../lib/characterTraits";
import { DISABILITY_OPTIONS } from "../lib/disabilities";

// Vanilla appearance palette codes (UI dropdowns; engine accepts custom codes too)
// Palette codes used to live as local arrays here; they moved into
// AppearancePaletteForm (the shared component used by both Create and
// Edit) as part of the palette-chip redesign.


// Vanilla AIM merc voices the player can borrow. The wizard doesn't ship
// voice files; in-game the merc plays whatever audio already sits in the
// Speech folder for its chosen voice index. JA2 looks for files named by
// voice index and bark event (e.g. 031_001.wav) and accepts .wav, .ogg, or
// .mp3. List drawn from vanilla AIM 0-39 — the most thoroughly-voiced mercs.
const VANILLA_VOICE_OPTIONS: ReadonlyArray<readonly [number, string]> = [
  [0, "Chosen One"], [1, "Sulik"], [2, "Trader"], [3, "Cassidy"],
  [4, "Ivan"], [5, "Steroid"], [6, "Wolf"], [7, "Grizzly"],
  [8, "Hitman"], [9, "Lynx"], [10, "Magic"], [11, "Stephen"],
  [12, "Scope"], [13, "Reaper"], [14, "Buns"], [15, "Tycho"],
  [16, "Buzz"], [17, "Raider"], [18, "Raven"], [19, "Static"],
  [20, "Len"], [21, "Danny"], [22, "Spider"], [23, "Igor"],
  [24, "Razor"], [25, "Fox"], [26, "Lynx (orig)"], [27, "Shadow"],
  [28, "Leech"], [29, "Numb"], [30, "Bull"], [31, "Vicki"],
  [32, "Nails"], [33, "Bubba"], [34, "Killian"], [35, "Fidel"],
  [36, "Dr. Q"], [37, "Meltdown"], [38, "Stogie"], [39, "Gus"],
];

function blankGearKit(): GearKit {
  return {
    mGearKitName: "Standard",
    mHelmet: 0, mVest: 0, mLeg: 0, mWeapon: 0,
    mBig0: 0, mBig0Status: 0, mBig0Quantity: 0,
    mBig1: 0, mBig1Status: 0, mBig1Quantity: 0,
    mBig2: 0, mBig2Status: 0, mBig2Quantity: 0,
    mBig3: 0, mBig3Status: 0, mBig3Quantity: 0,
    mSmall0: 0, mSmall1: 0, mSmall2: 0, mSmall3: 0,
    mSmall4: 0, mSmall5: 0, mSmall6: 0, mSmall7: 0,
    mPriceMod: 0,
    mAbsolutePrice: -1,
  };
}

// Default attribute starting point — moderate generalist
function makeBlankMerc(slot: number, faceIndex: number): Merc {
  return {
    uiIndex: slot,
    ubFaceIndex: faceIndex,
    Type: 1,
    zName: "",
    zNickname: "",
    bSex: 0,
    ubBodyType: 0,
    uiBodyTypeSubFlags: 0,
    usVoiceIndex: slot,
    bRace: 0,
    bNationality: 0,
    usEyesX: 10, usEyesY: 8,
    usMouthX: 7, usMouthY: 28,
    uiEyeDelay: 0, uiMouthDelay: 0,
    uiBlinkFrequency: 3000, uiExpressionFrequency: 2000,
    PANTS: "BROWNPANTS", VEST: "BROWNVEST",
    SKIN: "PINKSKIN", HAIR: "BROWNHEAD",
    bLifeMax: 80, bLife: 80,
    bStrength: 70, bAgility: 70, bDexterity: 70, bWisdom: 70,
    bExpLevel: 3,
    bEvolution: 0,
    bMarksmanship: 70, bExplosive: 20, bLeadership: 30,
    bMedical: 15, bMechanical: 20,
    fRegresses: 0,
    GrowthModifierLife: 0,
    GrowthModifierStrength: 0, GrowthModifierAgility: 0,
    GrowthModifierDexterity: 0, GrowthModifierWisdom: 0,
    GrowthModifierMarksmanship: 0, GrowthModifierExplosive: 0,
    GrowthModifierLeadership: 0, GrowthModifierMedical: 0,
    GrowthModifierMechanical: 0, GrowthModifierExpLevel: 0,
    bOldSkillTrait: 0, bOldSkillTrait2: 0,
    bNewSkillTrait1: 0, bNewSkillTrait2: 0,
    bNewSkillTrait3: 0, bNewSkillTrait4: 0,
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

type Step = "slot" | "identity" | "portrait" | "attributes" | "traits" | "biography" | "gear" | "voice" | "review";
const STEPS: { id: Step; label: string }[] = [
  { id: "slot", label: "Slot" },
  { id: "identity", label: "Identity" },
  { id: "portrait", label: "Portrait" },
  { id: "attributes", label: "Attributes" },
  { id: "traits", label: "Traits" },
  { id: "biography", label: "Biography" },
  { id: "gear", label: "Gear" },
  { id: "voice", label: "Voice" },
  { id: "review", label: "Review" },
];


function FileInput({
  value,
  onChange,
  label,
}: {
  value: File | null;
  onChange: (f: File | null) => void;
  label?: string;
}) {
  return (
    <label className="block">
      {label && <div className="text-xs text-wasteland-300 mb-1">{label}</div>}
      <div className="flex items-center gap-2">
        <input
          type="file"
          // Match PortraitDropzone's accept list so the main and advanced
          // upload slots take the same image formats. Previously the main
          // dropzone allowed BMP but the advanced FileInputs rejected it,
          // producing inconsistent UX between sections.
          accept="image/png,image/jpeg,image/webp,image/bmp"
          onChange={(e) => onChange(e.target.files?.[0] ?? null)}
          className="block w-full text-xs file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:bg-wasteland-700 file:text-wasteland-100 hover:file:bg-wasteland-600 file:cursor-pointer"
          aria-label={label ?? "Choose file"}
        />
        {value && (
          <button
            type="button"
            className="text-xs text-rust-400 hover:text-rust-300"
            onClick={() => onChange(null)}
            title="Remove this file"
            aria-label={`Remove ${value.name}`}
          >
            ×
          </button>
        )}
      </div>
      {value && (
        <div className="text-xs text-wasteland-400 mt-1 truncate" title={value.name}>
          {value.name}
        </div>
      )}
    </label>
  );
}


export default function Create() {
  const [params] = useSearchParams();
  // Robust slot-param parsing. `?? 220` only catches null (param absent);
  // `?slot=` with an empty value gives `""`, and `Number("") === 0`.
  // Combined with `params.has("slot") === true` for the empty form, the
  // pre-fix code silently jumped past the slot picker straight to
  // Identity targeting slot 0 — vanilla AIM Barry — without ever
  // surfacing the slot-lock warnings. Bug-review finding D4. Also
  // guard against non-numeric `?slot=abc` (Number returns NaN, which
  // is not a usable uiIndex). Falls back to 220 only when the param is
  // absent OR present-but-invalid; the preselection flag also requires
  // the parsed slot to be in [0, 254].
  const rawSlotParam = params.get("slot");
  const parsedSlot = rawSlotParam !== null ? Number(rawSlotParam) : NaN;
  const slotIsValid = Number.isFinite(parsedSlot) && parsedSlot >= 0 && parsedSlot <= 254;
  const initialSlot = slotIsValid ? parsedSlot : 220;
  // When the user arrives here from the new Merc Wizard roster
  // (which always supplies ?slot=N), they've already picked the slot
  // — skip the slot step entirely and drop them on Identity. The slot
  // step is still reachable via the Back nav for the rare case where
  // they want to change it. If no ?slot= param (or it's invalid), fall
  // back to the old behavior where Step 1 IS the slot picker.
  const slotPreselected = slotIsValid;

  const [step, setStep] = useState<Step>(slotPreselected ? "identity" : "slot");
  const initialBlankMerc = useMemo(
    () => makeBlankMerc(initialSlot, initialSlot),
    [initialSlot],
  );
  const [merc, setMerc] = useState<Merc>(initialBlankMerc);
  const [portrait, setPortrait] = useState<File | null>(null);
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null);
  // Dirty = any divergence from the blank starting state OR a portrait
  // picked. Cheap deep-equality on the Merc model (no functions, all
  // primitives).
  const dirty = useMemo(() => {
    if (portrait) return true;
    return JSON.stringify(merc) !== JSON.stringify(initialBlankMerc);
  }, [merc, initialBlankMerc, portrait]);
  const { confirmNavigate } = useUnsavedGuard(dirty);
  // Optional alternate-authoring uploads, all independent. See WMERC_FORMAT.md
  // for the "richer authoring" section. Each File slot maps 1:1 to a
  // multipart field on POST /portrait/compile.
  const [bigfaceImage, setBigfaceImage] = useState<File | null>(null);
  const [eyeFrames, setEyeFrames] = useState<(File | null)[]>([null, null, null, null]);
  const [mouthFrames, setMouthFrames] = useState<(File | null)[]>([null, null, null]);
  // Sub-frame region (position + size) for the SmallFace animation strip.
  // Position writes to MercProfiles.xml's usEyesX/Y / usMouthX/Y; size becomes
  // the STI sub-frame dimensions (engine reads from per-frame header).
  // w/h = 0 means "use vanilla canonical (17×6 / 14×6)".
  const [eyeBox, setEyeBox] = useState<SubframeBox>({ x: 10, y: 8, w: 17, h: 6 });
  const [mouthBox, setMouthBox] = useState<SubframeBox>({ x: 7, y: 28, w: 14, h: 6 });

  // Revoke the portrait preview URL on unmount (or when it changes). Without
  // this each Create session leaks a blob URL of the source image, which on
  // a 4MP camera PNG is multiple MB. The new-File replacement path inside
  // PortraitDropzone's onFileSelected already revokes the previous URL
  // before assigning a new one, but the FINAL URL outlives the component if
  // the user navigates away mid-Create without ever picking again.
  useEffect(() => {
    return () => {
      if (portraitUrl) URL.revokeObjectURL(portraitUrl);
    };
  }, [portraitUrl]);
  // Default gear: empty stub (unarmed). User picks a preset in the Gear step.
  const [gearKit, setGearKit] = useState<GearKit>(() => blankGearKit());
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);

  const presets = useQuery({ queryKey: ["gear-presets"], queryFn: listGearPresets });
  const qc = useQueryClient();
  const lockGuard = useSlotLockGuard();

  const compile = useMutation({
    mutationFn: async () => {
      if (!portrait) throw new Error("No portrait selected");
      const portraitRes = await compilePortrait(portrait, merc.ubFaceIndex, {
        eye_x: eyeBox.x,
        eye_y: eyeBox.y,
        eye_w: eyeBox.w,
        eye_h: eyeBox.h,
        mouth_x: mouthBox.x,
        mouth_y: mouthBox.y,
        mouth_w: mouthBox.w,
        mouth_h: mouthBox.h,
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
      // Pass `aim_binding: null` for Type=1 (AIM) mercs and let the server
      // derive the canonical AimBioID via `aim_availability.compute_aim_bio_id`.
      // Hardcoding 71 here clobbered the canonical lookup for expanded-AIM
      // slots — see bug-sweep #44.
      const mercRes = await createMerc({
        // Sync eye/mouth position to the merc profile before write — the
        // picker is the source of truth for these coords.
        merc: {
          ...merc,
          usEyesX: eyeBox.x,
          usEyesY: eyeBox.y,
          usMouthX: mouthBox.x,
          usMouthY: mouthBox.y,
        },
        // Server will fill aim_binding when Type==1 and the wizard hasn't supplied one
        aim_binding: undefined,
        gear: {
          mIndex: merc.uiIndex,
          mName: merc.zName,
          kits: [gearKit],
        },
      });
      return { portrait: portraitRes, merc: mercRes };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["roster"] });
      qc.invalidateQueries({ queryKey: ["backups"] });
      qc.invalidateQueries({ queryKey: ["slot", merc.uiIndex] });
      qc.invalidateQueries({ queryKey: ["voice", merc.uiIndex] });
      // TanStack Query matches keys literally — invalidating ["roster"]
      // does NOT invalidate ["slot-picker", ...]. Without this explicit
      // call, the slot picker's 30s staleTime kept showing the just-
      // written slot as empty across rapid Create cycles, letting the
      // user overwrite their own merc without warning. Bug-review
      // finding E4.
      qc.invalidateQueries({ queryKey: ["slot-picker"] });
    },
  });

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Create New Merc</h1>
        <button
          type="button"
          className="btn-ghost text-sm"
          onClick={() => confirmNavigate("/")}
        >
          ← Back to Hub
        </button>
      </div>

      <ol className="flex flex-wrap gap-2 mb-6">
        {STEPS.map((s, idx) => (
          <li key={s.id}>
            <button
              type="button"
              className={`px-3 py-1 rounded text-sm ${
                s.id === step
                  ? "bg-rust-500 text-wasteland-50"
                  : "bg-wasteland-800 text-wasteland-300 hover:bg-wasteland-700"
              }`}
              onClick={() => setStep(s.id)}
            >
              {idx + 1}. {s.label}
            </button>
          </li>
        ))}
      </ol>

      {step === "slot" && (
        <div className="card">
          <StartFromExistingPicker
            targetSlot={merc.uiIndex}
            targetFaceIndex={merc.ubFaceIndex}
            onPick={setMerc}
          />
          <h2 className="text-lg font-semibold mb-3">Pick a slot</h2>
          <div className="mb-3 flex items-center gap-3">
            <span className="text-sm text-wasteland-300">Hire type:</span>
            <div className="inline-flex rounded border border-wasteland-700 overflow-hidden">
              <button
                type="button"
                className={`px-3 py-1 text-sm transition-colors ${
                  merc.Type === 1
                    ? "bg-rust-500 text-wasteland-950 font-medium"
                    : "bg-wasteland-800 text-wasteland-200 hover:bg-wasteland-700"
                }`}
                onClick={() => setMerc({ ...merc, Type: 1 })}
              >
                AIM
              </button>
              <button
                type="button"
                className={`px-3 py-1 text-sm transition-colors ${
                  merc.Type === 2
                    ? "bg-rust-500 text-wasteland-950 font-medium"
                    : "bg-wasteland-800 text-wasteland-200 hover:bg-wasteland-700"
                }`}
                onClick={() => setMerc({ ...merc, Type: 2 })}
              >
                M.E.R.C.
              </button>
            </div>
            <span className="text-xs text-wasteland-500">
              {merc.Type === 1
                ? "Listed on the A.I.M. hiring site in the laptop · profile writes to AIMBIOS.EDT"
                : "Listed on Speck's M.E.R.C. hiring site in the laptop · profile writes to MERCBIOS.EDT"}
            </span>
          </div>
          <p className="text-sm text-wasteland-300 mb-4">
            Highlighted slots are vanilla 1.13 {merc.Type === 1 ? "AIM" : "M.E.R.C."}-bound. Filled slots are disabled.
          </p>
          <SlotPicker
            selected={merc.uiIndex}
            onSelect={(slot) => setMerc({
              ...merc,
              uiIndex: slot,
              ubFaceIndex: slot,
              // Default voice donor follows the slot so the merc sounds like
              // whoever previously occupied this slot (vanilla Lynx at 26, etc).
              // The Identity step lets the user override.
              usVoiceIndex: slot,
            })}
            showOnly={merc.Type === 2 ? "merc" : "aim"}
          />
          <div className="mt-4 text-sm text-wasteland-300">
            Selected: <span className="font-mono text-rust-400">slot {merc.uiIndex}</span>
            <span className="ml-3">
              ubFaceIndex: <span className="font-mono">{merc.ubFaceIndex}</span>
            </span>
            <span className="ml-3">
              Type: <span className="font-mono">{merc.Type === 1 ? "AIM" : "M.E.R.C."}</span>
            </span>
          </div>
          <FaceGearCapacityBanner faceIndex={merc.ubFaceIndex} />
        </div>
      )}

      {step === "identity" && (
        <div className="card grid grid-cols-2 gap-4">
          <label className="block col-span-2">
            <span className="text-sm font-medium text-wasteland-100">Full name</span>
            <input
              className="input mt-1"
              value={merc.zName}
              onChange={(e) => setMerc({ ...merc, zName: e.target.value })}
              maxLength={50}
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-wasteland-100">Nickname (max 9 chars)</span>
            <input
              className="input mt-1"
              value={merc.zNickname}
              onChange={(e) => setMerc({ ...merc, zNickname: e.target.value })}
              maxLength={9}
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-wasteland-100">Sex</span>
            <select
              className="input mt-1"
              value={merc.bSex}
              onChange={(e) => setMerc({ ...merc, bSex: Number(e.target.value) as 0 | 1 })}
            >
              <option value={0}>Male</option>
              <option value={1}>Female</option>
            </select>
          </label>
          <label className="block">
            <span className="text-sm font-medium text-wasteland-100">Type</span>
            <select
              className="input mt-1"
              value={merc.Type}
              onChange={(e) => setMerc({ ...merc, Type: Number(e.target.value) as Merc["Type"] })}
            >
              <option value={1}>AIM</option>
              <option value={2}>MERC</option>
              <option value={3}>RPC</option>
              <option value={4}>NPC</option>
            </select>
          </label>
          <label className="block col-span-2">
            <span className="text-sm font-medium text-wasteland-100">
              Voice donor (which merc's voice files this merc plays in-game)
            </span>
            <select
              className="input mt-1"
              value={merc.usVoiceIndex}
              onChange={(e) => setMerc({ ...merc, usVoiceIndex: Number(e.target.value) })}
            >
              <option value={merc.uiIndex}>This slot ({merc.uiIndex}) — silent until you add voice files</option>
              {/* Filter out merc.uiIndex from the vanilla list to avoid an
                  HTML-invalid duplicate <option value=N> if the user picked
                  a slot 0-39 in step 1. Without the filter React emits a
                  console warning and the controlled <select> behaves
                  unpredictably (browser picks whichever option's selected
                  flag fires last). */}
              {VANILLA_VOICE_OPTIONS.filter(([idx]) => idx !== merc.uiIndex).map(([idx, name]) => (
                <option key={idx} value={idx}>
                  Slot {idx} — {name}
                </option>
              ))}
            </select>
            <p className="text-xs text-wasteland-400 mt-1">
              Set a donor matching the personality you want, OR set it to this slot
              ({merc.uiIndex}) and upload your own voice files on the Voice step.
            </p>
            <VoiceIndexHint voiceIndex={merc.usVoiceIndex} />
          </label>

          <label className="block">
            <span className="text-sm font-medium text-wasteland-100">Body type</span>
            <select
              className="input mt-1"
              value={merc.ubBodyType}
              onChange={(e) => setMerc({ ...merc, ubBodyType: Number(e.target.value) })}
            >
              <option value={0}>Regular Male</option>
              <option value={1}>Big Male</option>
              <option value={2}>Stocky Male</option>
              <option value={3}>Regular Female</option>
            </select>
          </label>

          <label className="block">
            <span className="text-sm font-medium text-wasteland-100">Race</span>
            <select
              className="input mt-1"
              value={merc.bRace}
              onChange={(e) => setMerc({ ...merc, bRace: Number(e.target.value) })}
            >
              {RACE_OPTIONS.map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </select>
          </label>

          <label className="block col-span-2">
            <span className="text-sm font-medium text-wasteland-100">Nationality</span>
            <select
              className="input mt-1"
              value={merc.bNationality}
              onChange={(e) => setMerc({ ...merc, bNationality: Number(e.target.value) })}
            >
              {NATIONALITY_OPTIONS.map(([id, name]) => (
                <option key={id} value={id}>{id}. {name}</option>
              ))}
            </select>
          </label>

          <div className="col-span-2">
            <AppearancePaletteForm
              merc={merc}
              onChange={(field, value) => setMerc({ ...merc, [field]: value })}
            />
          </div>

          <fieldset className="block col-span-2 border border-wasteland-700 rounded p-3">
            <legend className="text-sm font-medium text-wasteland-100 px-1">
              Personality
            </legend>
            <div className="grid grid-cols-3 gap-3 mt-2">
              <label className="block">
                <span className="text-xs text-wasteland-300">Attitude</span>
                <select
                  className="input mt-1"
                  value={merc.bAttitude}
                  onChange={(e) => setMerc({ ...merc, bAttitude: Number(e.target.value) })}
                >
                  {ATTITUDE_OPTIONS.map(([id, name]) => (
                    <option key={id} value={id}>{name}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-wasteland-300">Character trait</span>
                <select
                  className="input mt-1"
                  value={merc.bCharacterTrait}
                  onChange={(e) => setMerc({ ...merc, bCharacterTrait: Number(e.target.value) })}
                >
                  {CHARACTER_TRAIT_OPTIONS.map(([id, name]) => (
                    <option key={id} value={id}>{name}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-wasteland-300">Disability</span>
                <select
                  className="input mt-1"
                  value={merc.bDisability}
                  onChange={(e) => setMerc({ ...merc, bDisability: Number(e.target.value) })}
                >
                  {DISABILITY_OPTIONS.map(([id, name]) => (
                    <option key={id} value={id}>{name}</option>
                  ))}
                </select>
              </label>
            </div>
          </fieldset>
        </div>
      )}

      {step === "portrait" && (
        <div className="space-y-4">
          <div className="card">
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
            <p className="text-xs text-wasteland-400 mt-3 text-center">
              Required. The wizard derives all 4 STI sizes (BigFace, SmallFace, 65Face, 33Face)
              from this one image. Skip mode is the default — the merc will be static in-game.
            </p>
          </div>

          <div className="card">
            <div className="mb-3">
              <h3 className="text-sm font-semibold text-wasteland-100">
                Eye &amp; mouth regions
              </h3>
              <p className="text-xs text-wasteland-400 mt-1">
                Tells the engine where on the 48×43 portrait to render the blink and talk
                animation strips. Use the vanilla preset for face-centered portraits or drag
                rectangles for non-standard framing.
              </p>
            </div>
            <EyeMouthPicker
              portrait={portrait}
              eyeBox={eyeBox}
              mouthBox={mouthBox}
              onEyeBoxChange={setEyeBox}
              onMouthBoxChange={setMouthBox}
            />
          </div>

          <details className="card">
            <summary className="cursor-pointer font-medium text-wasteland-200">
              Advanced: animation frames + separate BigFace (optional)
            </summary>
            <div className="mt-4 space-y-5 text-sm">
              <div>
                <div className="font-medium mb-2 text-wasteland-200">BigFace source (106×122 hero portrait)</div>
                <p className="text-xs text-wasteland-400 mb-2">
                  Use a different framing than the main portrait — wider composition, more shoulders,
                  etc. The 65Face and 33Face still come from the main image.
                </p>
                <FileInput value={bigfaceImage} onChange={setBigfaceImage} />
              </div>

              <AnimationFrameStrip
                eyeFrames={eyeFrames}
                mouthFrames={mouthFrames}
                onEyeFramesChange={setEyeFrames}
                onMouthFramesChange={setMouthFrames}
              />
            </div>
          </details>
        </div>
      )}

      {step === "attributes" && (
        <div className="space-y-4">
          <div className="card grid grid-cols-2 gap-x-6 gap-y-3">
            <StatSlider label="Strength" value={merc.bStrength} onChange={(v) => setMerc({ ...merc, bStrength: v })} />
            <StatSlider label="Agility" value={merc.bAgility} onChange={(v) => setMerc({ ...merc, bAgility: v })} />
            <StatSlider label="Dexterity" value={merc.bDexterity} onChange={(v) => setMerc({ ...merc, bDexterity: v })} />
            <StatSlider label="Wisdom" value={merc.bWisdom} onChange={(v) => setMerc({ ...merc, bWisdom: v })} />
            <StatSlider label="Marksmanship" value={merc.bMarksmanship} onChange={(v) => setMerc({ ...merc, bMarksmanship: v })} />
            <StatSlider label="Explosives" value={merc.bExplosive} onChange={(v) => setMerc({ ...merc, bExplosive: v })} />
            <StatSlider label="Leadership" value={merc.bLeadership} onChange={(v) => setMerc({ ...merc, bLeadership: v })} />
            <StatSlider label="Medical" value={merc.bMedical} onChange={(v) => setMerc({ ...merc, bMedical: v })} />
            <StatSlider label="Mechanical" value={merc.bMechanical} onChange={(v) => setMerc({ ...merc, bMechanical: v })} />
            <StatSlider label="Experience Level" value={merc.bExpLevel} onChange={(v) => setMerc({ ...merc, bExpLevel: v })} min={1} max={10} />
            <StatSlider label="Life Max (HP)" value={merc.bLifeMax} onChange={(v) => setMerc({ ...merc, bLifeMax: v, bLife: v })} min={1} max={150} />
            <StatSlider label="Death Rate (% perm-death risk)" value={merc.bDeathRate} onChange={(v) => setMerc({ ...merc, bDeathRate: v })} min={0} max={100} />
          </div>
          <div className="card grid grid-cols-3 gap-x-4 gap-y-3">
            <div className="col-span-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-wasteland-100">Hire cost (AIM/MERC)</h3>
              <button
                type="button"
                className="text-xs text-rust-400 hover:text-rust-200"
                onClick={() => {
                  const s = suggestSalary(merc);
                  setMerc({
                    ...merc,
                    sSalary: s.daily,
                    uiWeeklySalary: s.weekly,
                    uiBiWeeklySalary: s.biWeekly,
                  });
                }}
                title="Compute salaries from this merc's stats + level using the vanilla AIM tier curve"
              >
                Suggest from stats
              </button>
            </div>
            {(() => {
              const { outOfBand, suggestion } = salaryIsOutOfBand(merc);
              if (!outOfBand) return null;
              return (
                <div className="col-span-3 rounded border border-yellow-500/40 bg-yellow-500/10 px-2 py-1.5 text-xs text-yellow-300">
                  ⚠ Salaries diverge &gt;50% from the AIM tier curve for this merc's stats
                  (avg combat stat {suggestion.averageCombatStat}, L{merc.bExpLevel}). Suggestion:{" "}
                  ${suggestion.daily}/day, ${suggestion.weekly}/wk, ${suggestion.biWeekly}/bi-wk.
                </div>
              );
            })()}
            <label className="block">
              <span className="text-xs text-wasteland-300">Daily salary ($)</span>
              <input
                type="number"
                className="input mt-1"
                value={merc.sSalary}
                onChange={(e) => setMerc({ ...merc, sSalary: Number(e.target.value) || 0 })}
              />
            </label>
            <label className="block">
              <span className="text-xs text-wasteland-300">Weekly salary ($)</span>
              <input
                type="number"
                className="input mt-1"
                value={merc.uiWeeklySalary}
                onChange={(e) => setMerc({ ...merc, uiWeeklySalary: Number(e.target.value) || 0 })}
              />
            </label>
            <label className="block">
              <span className="text-xs text-wasteland-300">Bi-weekly salary ($)</span>
              <input
                type="number"
                className="input mt-1"
                value={merc.uiBiWeeklySalary}
                onChange={(e) => setMerc({ ...merc, uiBiWeeklySalary: Number(e.target.value) || 0 })}
              />
            </label>
            <label className="block col-span-3">
              <span className="text-xs text-wasteland-300">
                Medical deposit required (AIM only — refunded if merc survives the contract)
              </span>
              <select
                className="input mt-1 max-w-md"
                value={merc.bMedicalDeposit}
                onChange={(e) => setMerc({ ...merc, bMedicalDeposit: Number(e.target.value) as 0 | 1 })}
              >
                <option value={0}>No deposit</option>
                <option value={1}>Require deposit</option>
              </select>
            </label>
            {merc.bMedicalDeposit === 1 && (
              <label className="block">
                <span className="text-xs text-wasteland-300">Deposit amount ($)</span>
                <input
                  type="number"
                  className="input mt-1"
                  value={merc.sMedicalDepositAmount}
                  onChange={(e) => setMerc({ ...merc, sMedicalDepositAmount: Number(e.target.value) || 0 })}
                />
              </label>
            )}
          </div>
        </div>
      )}

      {step === "traits" && (
        <div className="card space-y-4">
          <div>
            <h2 className="text-lg font-semibold">Skill traits</h2>
            <p className="text-sm text-wasteland-300 mt-1">
              The system in use (New / Old) is detected from this install's{" "}
              <code className="font-mono">Ja2_Options.ini</code>. Traits drive in-game class
              specialization — without them the merc fights at generic baseline.
            </p>
          </div>
          <TraitPicker merc={merc} onChange={setMerc} />
        </div>
      )}

      {step === "biography" && (
        <div className="card space-y-4">
          <BiographyEditor
            label="Biography (shown in AIM hiring screen)"
            value={merc.biographyText}
            onChange={(v) => setMerc({ ...merc, biographyText: v })}
            maxLength={400}
          />
          <BiographyEditor
            label="Additional info"
            value={merc.additionalInfoText}
            onChange={(v) => setMerc({ ...merc, additionalInfoText: v })}
            maxLength={160}
            rows={3}
          />
          <BackgroundPicker
            value={merc.usBackground}
            onChange={(id) => setMerc({ ...merc, usBackground: id })}
          />
        </div>
      )}

      {step === "gear" && (
        <div className="card space-y-4">
          <div>
            <h2 className="text-lg font-semibold">Starting gear</h2>
            <p className="text-sm text-wasteland-300 mt-1">
              Pick a preset loadout. Without one, the merc spawns unarmed — the engine still
              hires them, but they have nothing to fight with on day one.
            </p>
          </div>
          {presets.isLoading && <p className="text-sm text-wasteland-400">Loading presets...</p>}
          {presets.isError && (
            <p className="text-sm text-rust-400">
              Couldn't load gear presets. The merc will spawn unarmed unless you skip this step.
            </p>
          )}
          {presets.data && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <button
                type="button"
                className={`text-left rounded border p-3 transition-colors ${
                  selectedPresetId === null
                    ? "border-rust-500 bg-rust-500/10"
                    : "border-wasteland-700 hover:border-rust-500/50"
                }`}
                onClick={() => {
                  setSelectedPresetId(null);
                  setGearKit(blankGearKit());
                }}
              >
                <div className="font-medium">Unarmed (skip)</div>
                <div className="text-xs text-wasteland-400 mt-1">
                  No gear. Merc joins with empty hands. You can edit gear later.
                </div>
              </button>
              {presets.data.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`text-left rounded border p-3 transition-colors ${
                    selectedPresetId === p.id
                      ? "border-rust-500 bg-rust-500/10"
                      : "border-wasteland-700 hover:border-rust-500/50"
                  }`}
                  onClick={() => {
                    setSelectedPresetId(p.id);
                    setGearKit(p.gear as GearKit);
                  }}
                >
                  <div className="font-medium">{p.name}</div>
                  <div className="text-xs text-wasteland-400 mt-1">{p.description}</div>
                </button>
              ))}
            </div>
          )}
          {selectedPresetId && (
            <div className="text-xs text-wasteland-400 font-mono">
              Selected: weapon={gearKit.mWeapon}, vest={gearKit.mVest},
              helmet={gearKit.mHelmet}, ammo={gearKit.mBig0}×{gearKit.mBig0Quantity}
            </div>
          )}
        </div>
      )}

      {step === "voice" && (
        <div className="card space-y-4">
          <div>
            <h2 className="text-lg font-semibold">Voice clips (optional)</h2>
            <p className="text-sm text-wasteland-300 mt-1">
              Drop audio files here for this merc's hiring, combat, and social barks.
              <span className="text-wasteland-500"> Skip the whole step if you don't have any — the merc still works silent.</span>
            </p>

            {/* How-to checklist — replaces the old dense paragraph
                per bug #7. Three concrete steps + a naming-table
                disclosure so the user knows what goes where. */}
            <ol className="mt-3 space-y-1.5 text-sm text-wasteland-200 list-decimal list-inside">
              <li>
                <b>Pick a voice donor on Identity.</b> The merc plays
                whichever clips live at <code className="font-mono">Speech/{merc.usVoiceIndex}/</code>.
                Set the donor to a classic merc's index to inherit their lines,
                or set it to <code className="font-mono">{merc.uiIndex}</code> (this slot)
                to play your own uploads below.
              </li>
              <li>
                <b>Format your audio as <code className="font-mono">.wav</code> or <code className="font-mono">.ogg</code>.</b>{" "}
                JA2 reads either. MP3 is also accepted but converted on load.
              </li>
              <li>
                <b>Name files by event.</b> JA2 maps each filename to a specific
                bark slot (hiring, sighted, taking damage, etc.). Files with
                non-standard names sit on disk but never play.
              </li>
            </ol>

            <details className="mt-3 text-xs text-wasteland-400">
              <summary className="cursor-pointer hover:text-wasteland-200">
                Common bark-event suffixes (click to expand)
              </summary>
              <table className="mt-1.5 font-mono text-[11px]">
                <tbody>
                  <tr><td className="pr-3 text-rust-400">_001</td><td>hired / on-payroll greeting</td></tr>
                  <tr><td className="pr-3 text-rust-400">_010</td><td>target sighted (combat alert)</td></tr>
                  <tr><td className="pr-3 text-rust-400">_015</td><td>taking damage</td></tr>
                  <tr><td className="pr-3 text-rust-400">_020</td><td>kill confirm</td></tr>
                  <tr><td className="pr-3 text-rust-400">_030</td><td>low HP / wounded warning</td></tr>
                  <tr><td className="pr-3 text-rust-400">_040</td><td>downed / dying</td></tr>
                  <tr><td className="pr-3 text-rust-400">_050</td><td>social banter (between turns)</td></tr>
                </tbody>
              </table>
              <p className="mt-1.5 italic">
                Prefix the index with <code className="font-mono">MERCNNN_</code>
                (e.g. <code className="font-mono">MERC026_001.wav</code>) or just
                use <code className="font-mono">NNN.wav</code>. JA2 accepts both.
              </p>
            </details>

            {merc.usVoiceIndex !== merc.uiIndex && (
              <div className="mt-3 rounded border border-yellow-500/40 bg-yellow-500/10 p-2 text-xs text-yellow-300">
                ⚠ Your voice donor is set to <code className="font-mono">usVoiceIndex={merc.usVoiceIndex}</code>{" "}
                on the Identity step. In-game the merc will play from{" "}
                <code className="font-mono">Speech/{merc.usVoiceIndex}/</code>, NOT from the folder you're
                about to upload to (<code className="font-mono">Speech/{merc.uiIndex}/</code>). If you
                want these custom clips to be heard, go back to Identity and set the donor to{" "}
                <code className="font-mono">{merc.uiIndex}</code>.
                <button
                  type="button"
                  className="ml-2 underline hover:text-yellow-200"
                  onClick={() => setMerc({ ...merc, usVoiceIndex: merc.uiIndex })}
                >
                  Fix it
                </button>
              </div>
            )}
          </div>
          <VoiceFileManager slot={merc.uiIndex} />
        </div>
      )}

      {step === "review" && (
        <div className="card space-y-4">
          <h2 className="text-lg font-semibold">Review and write</h2>
          <div className="text-sm text-wasteland-200 grid grid-cols-2 gap-x-4 gap-y-1">
            <span className="text-wasteland-400">Slot</span><span className="font-mono">{merc.uiIndex}</span>
            <span className="text-wasteland-400">Face index</span><span className="font-mono">{merc.ubFaceIndex}</span>
            <span className="text-wasteland-400">Type</span><span>{merc.Type === 1 ? "AIM" : merc.Type === 2 ? "MERC" : "Other"}</span>
            <span className="text-wasteland-400">Name</span><span>{merc.zName || "(unset)"}</span>
            <span className="text-wasteland-400">Nickname</span><span>{merc.zNickname || "(unset)"}</span>
            <span className="text-wasteland-400">Portrait file</span><span className="truncate">{portrait?.name ?? "(none — required)"}</span>
            <span className="text-wasteland-400">Gear preset</span>
            <span>
              {selectedPresetId
                ? (presets.data?.find((p) => p.id === selectedPresetId)?.name ?? selectedPresetId)
                : <span className="text-yellow-400">Unarmed (no gear)</span>}
            </span>
          </div>
          {compile.data && (
            <AuditPanel issues={compile.data.merc.issues} />
          )}
          <button
            type="button"
            className="btn-primary"
            // Disable on isSuccess too so a quick second click doesn't re-run
            // the entire create-merc pipeline (double-backup, double-write).
            disabled={
              !portrait || !merc.zName || !merc.zNickname
              || compile.isPending || compile.isSuccess
            }
            onClick={() => lockGuard.guard(merc.uiIndex, () => compile.mutate())}
          >
            {compile.isPending && (
              <span
                className="inline-block mr-2 h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent align-middle"
                aria-hidden
              />
            )}
            {compile.isPending
              ? "Compiling..."
              : compile.isSuccess
                ? "Compiled ✓"
                : "Compile & write to game"}
          </button>
          {compile.isPending && (
            <div className="mt-2">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-wasteland-800">
                {/* Indeterminate progress — the compile pipeline isn't
                    streamed yet so we can't show a real percentage. An
                    animated bar gives the user something to look at
                    instead of a frozen-looking screen. */}
                <div className="h-full w-1/3 animate-pulse rounded-full bg-rust-500" />
              </div>
              <p className="mt-1 text-xs text-wasteland-400">
                Writing MercProfiles.xml, AIMAvailability.xml, MercStartingGear.xml,
                EDT bio, and the four portrait STIs… typically 1–3 seconds.
              </p>
            </div>
          )}
          {compile.isSuccess && compile.data.merc.ok && (
            <div className="text-sm text-green-400">
              ✓ Wrote merc to slot {compile.data.merc.slot}.{" "}
              {compile.data.portrait.files_written.length} STI files written.
            </div>
          )}
          {compile.isError && (
            <div className="text-sm text-rust-400">
              Compile failed. Check the audit panel above for blocking issues.
            </div>
          )}
        </div>
      )}

      <div className="mt-6 flex justify-between">
        <button
          type="button"
          className="btn-ghost"
          disabled={step === "slot"}
          onClick={() => {
            const idx = STEPS.findIndex((s) => s.id === step);
            const prev = STEPS[idx - 1];
            if (prev) setStep(prev.id);
          }}
        >
          ← Back
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={step === "review"}
          onClick={() => {
            const idx = STEPS.findIndex((s) => s.id === step);
            const next = STEPS[idx + 1];
            if (next) setStep(next.id);
          }}
        >
          Next →
        </button>
      </div>
      {lockGuard.pending && (
        <SlotLockWarningModal
          lock={lockGuard.pending.lock}
          action="create"
          onConfirm={lockGuard.confirm}
          onCancel={lockGuard.cancel}
        />
      )}
    </div>
  );
}
