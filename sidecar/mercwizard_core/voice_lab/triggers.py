"""Load and select engine-derived dialogue trigger meanings."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from .models import TriggerAlias, VoiceTrigger


@dataclass(frozen=True)
class _CatalogTrigger:
    slot: int
    aliases: tuple[TriggerAlias, ...]


class TriggerCatalog:
    """All dialogue slots, including aliases whose meaning depends on profile type."""

    def __init__(self, profile_types: dict[int, str], triggers: tuple[_CatalogTrigger, ...]) -> None:
        self._profile_types = profile_types
        self._triggers = {trigger.slot: trigger for trigger in triggers}

    def for_profile(self, slot: int, profile_type: int) -> VoiceTrigger:
        """Return ``slot`` with the alias that applies to ``profile_type``."""
        try:
            trigger = self._triggers[slot]
        except KeyError as exc:
            raise ValueError(f"unknown dialogue slot {slot}") from exc
        alias = _select_alias(trigger.aliases, self._profile_types.get(profile_type, ""))
        return VoiceTrigger(
            slot=slot,
            name=alias.name,
            meaning=alias.meaning,
            aliases=trigger.aliases,
        )


def _select_alias(aliases: tuple[TriggerAlias, ...], profile_name: str) -> TriggerAlias:
    if not aliases:
        raise ValueError("dialogue trigger has no aliases")
    if profile_name == "AIM":
        for alias in aliases:
            text = f"{alias.name} {alias.meaning}".upper()
            if "AIM" in text and "NOT AIM" not in text and "NON_AIM" not in text:
                return alias
    elif profile_name:
        for alias in aliases:
            text = f"{alias.name} {alias.meaning}".upper()
            if "NON_AIM" in text or "NOT AIM" in text:
                return alias
        for alias in aliases:
            if profile_name in f"{alias.name} {alias.meaning}".upper():
                return alias
    return aliases[0]


@lru_cache(maxsize=1)
def load_trigger_catalog() -> TriggerCatalog:
    """Load the packaged catalog generated from the two authoritative headers."""
    catalog_resource = resources.files("mercwizard_core").joinpath("data", "voice_triggers.json")
    payload = json.loads(catalog_resource.read_text(encoding="utf-8"))
    profile_types = {int(value): name for name, value in payload["profile_types"].items()}
    triggers = tuple(
        _CatalogTrigger(
            slot=entry["slot"],
            aliases=tuple(
                TriggerAlias(
                    name=alias["name"],
                    meaning=alias["meaning"],
                    source_comment=alias["source_comment"],
                )
                for alias in entry["aliases"]
            ),
        )
        for entry in payload["triggers"]
    )
    return TriggerCatalog(profile_types, triggers)
