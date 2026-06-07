import type { Campaign } from "../types/modpack";

// PORTABLE: This component is pure-React, no Tauri imports.
// MercForge will import it as-is and pass its own onSelect callback.

interface Props {
  campaign: Campaign;
  active: boolean;
  onSelect: (c: Campaign) => void;
}

export function CampaignCard({ campaign, active, onSelect }: Props) {
  return (
    <button
      onClick={() => onSelect(campaign)}
      className={[
        "text-left w-full p-4 rounded border transition-colors",
        active
          ? "border-ja2-accent bg-ja2-panel ring-1 ring-ja2-accent"
          : "border-ja2-border bg-ja2-panel hover:border-ja2-accent",
      ].join(" ")}
    >
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-lg font-semibold text-ja2-text">
          {campaign.display_name}
        </h3>
        <div className="flex items-center gap-2 text-xs">
          {campaign.difficulty_hint && (
            <span className="px-2 py-0.5 rounded bg-ja2-bg text-ja2-dim border border-ja2-border">
              {campaign.difficulty_hint}
            </span>
          )}
          {active && (
            <span className="px-2 py-0.5 rounded bg-ja2-accent text-ja2-bg font-medium">
              ACTIVE
            </span>
          )}
        </div>
      </div>
      <p className="mt-2 text-sm text-ja2-dim leading-relaxed">
        {campaign.description}
      </p>
      {campaign.estimated_playtime_hours != null && (
        <p className="mt-2 text-xs text-ja2-dim">
          ~{campaign.estimated_playtime_hours} hours of play
          {campaign.source?.version && ` · ${campaign.source.version}`}
        </p>
      )}
    </button>
  );
}
