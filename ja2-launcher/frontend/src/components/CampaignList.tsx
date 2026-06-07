import type { Campaign } from "../types/modpack";
import { CampaignCard } from "./CampaignCard";

// PORTABLE: pure-React, no Tauri imports.

interface Props {
  campaigns: Campaign[];
  activeVfsConfig: string;   // e.g. "vfs_config.AR.ini" — compared against each campaign.vfs_config
  onSelect: (c: Campaign) => void;
}

export function CampaignList({ campaigns, activeVfsConfig, onSelect }: Props) {
  return (
    <div className="flex flex-col gap-3">
      {campaigns.map((c) => (
        <CampaignCard
          key={c.id}
          campaign={c}
          active={c.vfs_config === activeVfsConfig}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
