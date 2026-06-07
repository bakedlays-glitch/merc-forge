import { useQuery } from "@tanstack/react-query";

import { probeVoiceIndex } from "../lib/api";

interface Props {
  voiceIndex: number;
}

/**
 * Probes the install for voice clips at the given index. If none exist, warns
 * that the merc will be silent in combat. Silently renders nothing while
 * loading or on error (the merc still works without voice — this is advisory).
 */
export default function VoiceIndexHint({ voiceIndex }: Props) {
  const probe = useQuery({
    queryKey: ["voice-probe", voiceIndex],
    queryFn: () => probeVoiceIndex(voiceIndex),
    staleTime: 60 * 1000,
  });

  if (probe.isLoading || probe.isError || !probe.data) return null;
  const { clip_count, folder_exists, slf_clip_count, is_vanilla_archive } = probe.data;
  // Loose clips exist → unambiguous OK.
  if (clip_count > 0) {
    return (
      <p className="text-xs text-wasteland-500 mt-1">
        ✓ Voice index {voiceIndex} has {clip_count} clip{clip_count === 1 ? "" : "s"} in this install.
      </p>
    );
  }
  // No loose clips, but Speech.slf carries this index → vanilla donor
  // whose barks live inside the game's speech archive. The engine
  // reads from the SLF at runtime; merc will NOT be silent. Reassure
  // the user instead of warning them. (Bug #4 fix.)
  if (is_vanilla_archive && slf_clip_count && slf_clip_count > 0) {
    return (
      <p className="text-xs text-emerald-300/90 mt-1">
        ✓ Voice index {voiceIndex} is provided by the base game's
        Speech.slf archive ({slf_clip_count} clip
        {slf_clip_count === 1 ? "" : "s"}). No loose files needed.
      </p>
    );
  }
  return (
    <p className="text-xs text-yellow-400/90 mt-1">
      ⚠ Voice index {voiceIndex} has no voice files in this install
      {folder_exists ? " (folder exists but is empty)" : " (folder doesn't exist)"}.
      This merc won't have their own spoken lines — hire and conversation lines,
      or combat shouts — at this index. The game falls back to a couple of
      generic sounds (you'll still hear a death cry), but the merc will be mostly
      silent. The simplest fix is to pick a different voice donor on the Identity
      step. You can also add your own conversation clips on the Voice step, though
      those cover dialogue only, not combat shouts.
    </p>
  );
}
