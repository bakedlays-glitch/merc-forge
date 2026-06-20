"""Tests for the XML writers: profiles_xml, aim_availability, starting_gear."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mercwizard_core.inject import aim_availability, merc_availability, profiles_xml, starting_gear
from mercwizard_core.models import AimBinding, Gear, GearKit, Merc, MercBinding


# ──────────────────────────────────────────────────────────────────────────
#  MercProfiles.xml
# ──────────────────────────────────────────────────────────────────────────

def test_profiles_upsert_creates_file_if_missing(tmp_path: Path, sample_merc: Merc) -> None:
    xml = tmp_path / "MercProfiles.xml"
    profiles_xml.upsert(xml, sample_merc)
    assert xml.is_file()
    contents = xml.read_text()
    assert "<uiIndex>220</uiIndex>" in contents
    assert "<zName>Tycho</zName>" in contents


def test_profiles_round_trip(tmp_path: Path, sample_merc: Merc) -> None:
    xml = tmp_path / "MercProfiles.xml"
    profiles_xml.upsert(xml, sample_merc)
    read = profiles_xml.read_slot(xml, 220)
    assert read is not None
    assert read["zName"] == "Tycho"
    assert read["uiIndex"] == "220"


def test_profiles_upsert_updates_existing(tmp_path: Path, sample_merc: Merc) -> None:
    xml = tmp_path / "MercProfiles.xml"
    profiles_xml.upsert(xml, sample_merc)
    # Update the same slot with a different name
    updated = sample_merc.model_copy(update={"zName": "TychoUpdated"})
    profiles_xml.upsert(xml, updated)
    read = profiles_xml.read_slot(xml, 220)
    assert read is not None
    assert read["zName"] == "TychoUpdated"


def test_profiles_clear_slot(tmp_path: Path, sample_merc: Merc) -> None:
    xml = tmp_path / "MercProfiles.xml"
    profiles_xml.upsert(xml, sample_merc)
    assert profiles_xml.clear_slot(xml, 220) is True
    assert profiles_xml.read_slot(xml, 220) is None


def test_profiles_read_all_slots(tmp_path: Path, sample_merc: Merc) -> None:
    xml = tmp_path / "MercProfiles.xml"
    profiles_xml.upsert(xml, sample_merc)
    second = sample_merc.model_copy(update={"uiIndex": 221, "zName": "Other"})
    profiles_xml.upsert(xml, second)
    all_slots = profiles_xml.read_all_slots(xml)
    assert set(all_slots.keys()) == {220, 221}
    assert all_slots[221]["zName"] == "Other"


def test_profiles_schema_aware_writer_skips_fields_not_in_install(
    tmp_path: Path, sample_merc: Merc
) -> None:
    """Schema-aware upsert: when the install's existing profiles don't carry a
    field (e.g. Arulco Revisited pre-STOMP), the wizard must NOT inject it.
    """
    from lxml import etree
    pre_stomp = tmp_path / "pre_stomp.xml"
    pre_stomp.write_bytes(
        b"<MERCPROFILES>\n"
        b"  <PROFILE>\n"
        b"    <uiIndex>1</uiIndex>\n"
        b"    <zName>Existing</zName>\n"
        b"    <zNickname>Old</zNickname>\n"
        b"    <Type>1</Type>\n"
        b"    <bSex>0</bSex>\n"
        b"    <ubBodyType>0</ubBodyType>\n"
        b"    <bStrength>50</bStrength>\n"
        b"  </PROFILE>\n"
        b"</MERCPROFILES>\n"
    )

    detected = profiles_xml.detect_schema(pre_stomp)
    assert detected is not None
    assert "bRace" not in detected
    assert "usBackground" not in detected

    profiles_xml.upsert(pre_stomp, sample_merc)
    tree = etree.parse(str(pre_stomp))
    new_block = None
    for entry in tree.getroot().findall("PROFILE"):
        ui_elem = entry.find("uiIndex")
        if ui_elem is not None and ui_elem.text and ui_elem.text.strip() == str(sample_merc.uiIndex):
            new_block = entry
            break
    assert new_block is not None
    # STOMP fields the install didn't carry must NOT be in the new block
    assert new_block.find("bRace") is None, "bRace leaked into pre-STOMP install"
    assert new_block.find("usBackground") is None, "usBackground leaked into pre-STOMP install"
    # But always-write core must be present
    assert new_block.find("zName") is not None
    assert new_block.find("bStrength") is not None


def test_profiles_full_schema_written_when_install_is_empty(
    tmp_path: Path, sample_merc: Merc
) -> None:
    """When the install has no existing profiles, the writer falls back to the
    full writable set — no schema to intersect against."""
    from lxml import etree
    empty_path = tmp_path / "fresh.xml"
    profiles_xml.upsert(empty_path, sample_merc)
    tree = etree.parse(str(empty_path))
    new_block = tree.getroot().find("PROFILE")
    assert new_block is not None
    # Full schema includes the STOMP fields
    assert new_block.find("bRace") is not None
    assert new_block.find("usBackground") is not None


# ──────────────────────────────────────────────────────────────────────────
#  Growth modifiers: engine tag is <bGrowthModifier*> (b-prefixed), the model
#  + TS schema use the prefix-less GrowthModifier*. These guard the read/edit/
#  write round-trip against the tag-name mismatch that silently dropped every
#  growth-modifier edit (XML_Profiles.cpp:105-115 parser; struct soldier
#  profile type.h:1044-1054).
# ──────────────────────────────────────────────────────────────────────────

def _find_profile(xml: Path, ui_index: int):
    from lxml import etree
    for entry in etree.parse(str(xml)).getroot().findall("PROFILE"):
        ui = entry.find("uiIndex")
        if ui is not None and (ui.text or "").strip() == str(ui_index):
            return entry
    return None


# Minimal valid-Merc skeleton shared by the growth-modifier tests so an
# AIMNAS/Vengeance-style fixture parses into a Merc (uiIndex/ubFaceIndex/
# zName/zNickname are the required fields).
_GM_FIXTURE_BODY = (
    b"    <ubFaceIndex>5</ubFaceIndex>\n"
    b"    <Type>1</Type>\n"
    b"    <bSex>0</bSex>\n"
    b"    <ubBodyType>0</ubBodyType>\n"
    b"    <usVoiceIndex>5</usVoiceIndex>\n"
    b"    <bLifeMax>50</bLifeMax>\n"
    b"    <bLife>50</bLife>\n"
    b"    <bStrength>50</bStrength>\n"
    b"    <bAgility>50</bAgility>\n"
    b"    <bDexterity>50</bDexterity>\n"
    b"    <bWisdom>50</bWisdom>\n"
    b"    <bExpLevel>1</bExpLevel>\n"
    b"    <bMarksmanship>50</bMarksmanship>\n"
    b"    <bExplosive>0</bExplosive>\n"
    b"    <bLeadership>0</bLeadership>\n"
    b"    <bMedical>0</bMedical>\n"
    b"    <bMechanical>0</bMechanical>\n"
)


def test_growth_modifier_round_trips_to_b_prefixed_tag_on_aimnas_install(
    tmp_path: Path,
) -> None:
    """The task's core regression: an install whose MercProfiles.xml already
    uses the engine's <bGrowthModifier*> tags (AIMNAS, Vengeance). Read a
    non-zero growth modifier, edit it, write — the on-disk tag must STAY
    b-prefixed with the new value, never dropped or written prefix-less.
    """
    xml = tmp_path / "MercProfiles.xml"
    xml.write_bytes(
        b"<MERCPROFILES>\n"
        b"  <PROFILE>\n"
        b"    <uiIndex>5</uiIndex>\n"
        b"    <zName>Existing</zName>\n"
        b"    <zNickname>Old</zNickname>\n"
        + _GM_FIXTURE_BODY
        + b"    <bGrowthModifierStrength>7</bGrowthModifierStrength>\n"
        b"    <bGrowthModifierMarksmanship>3</bGrowthModifierMarksmanship>\n"
        b"  </PROFILE>\n"
        b"</MERCPROFILES>\n"
    )

    # READ: the install's detected schema carries the engine's b-prefixed tag,
    # NOT the prefix-less spelling.
    detected = profiles_xml.detect_schema(xml)
    assert detected is not None
    assert "bGrowthModifierStrength" in detected
    assert "GrowthModifierStrength" not in detected

    # READ: raw read is the on-disk (b-prefixed) tag; normalization surfaces it
    # under the model/frontend field name with the real value intact.
    raw = profiles_xml.read_slot(xml, 5)
    assert raw is not None and raw["bGrowthModifierStrength"] == "7"
    norm = profiles_xml.normalize_profile_tags(raw)
    assert norm["GrowthModifierStrength"] == "7"
    assert "bGrowthModifierStrength" not in norm

    # Build the Merc the way the edit / relocator / export paths do (normalize,
    # then coerce), confirming the non-zero value survives into the model.
    string_fields = {"zName", "zNickname", "PANTS", "VEST", "SKIN", "HAIR",
                     "biographyText", "additionalInfoText"}
    kwargs: dict = {}
    for k, v in norm.items():
        if k not in Merc.model_fields:
            continue
        kwargs[k] = v if k in string_fields else int(v)
    merc = Merc(**kwargs)
    assert merc.GrowthModifierStrength == 7

    # EDIT + WRITE back into the same b-tag install.
    edited = merc.model_copy(update={"GrowthModifierStrength": 25})
    profiles_xml.upsert(xml, edited)

    block = _find_profile(xml, 5)
    assert block is not None
    # Engine-readable b-prefixed tag carries the NEW value...
    bnode = block.find("bGrowthModifierStrength")
    assert bnode is not None and bnode.text == "25", (
        "growth-modifier edit dropped or written to wrong tag"
    )
    # ...and there is NO prefix-less duplicate the engine would ignore.
    assert block.find("GrowthModifierStrength") is None
    # The untouched modifier stays b-prefixed and unchanged.
    assert block.find("bGrowthModifierMarksmanship").text == "3"

    # READ AGAIN: the new value surfaces after normalization on reload.
    norm2 = profiles_xml.normalize_profile_tags(profiles_xml.read_slot(xml, 5))
    assert norm2["GrowthModifierStrength"] == "25"


def test_growth_modifier_written_b_prefixed_in_fresh_file(
    tmp_path: Path, sample_merc: Merc
) -> None:
    """A new/blank install must get the engine's b-prefixed growth tag, or the
    game ignores it at load (the prefix-less tag matches no parser branch)."""
    xml = tmp_path / "fresh.xml"
    profiles_xml.upsert(xml, sample_merc.model_copy(update={"GrowthModifierAgility": -10}))
    block = _find_profile(xml, sample_merc.uiIndex)
    assert block is not None
    bnode = block.find("bGrowthModifierAgility")
    assert bnode is not None and bnode.text == "-10"
    assert block.find("GrowthModifierAgility") is None


def test_growth_modifier_self_heals_stale_prefixless_tag(tmp_path: Path) -> None:
    """A pre-fix MercWizard could write a prefix-less <GrowthModifier*> into a
    fresh file. On the next save the writer must emit the engine's b-tag AND
    drop the stale prefix-less duplicate, so it isn't re-detected as the
    install's schema (which would re-skip the real b-tag forever)."""
    xml = tmp_path / "stale.xml"
    xml.write_bytes(
        b"<MERCPROFILES>\n"
        b"  <PROFILE>\n"
        b"    <uiIndex>7</uiIndex>\n"
        b"    <zName>Stale</zName>\n"
        b"    <zNickname>St</zNickname>\n"
        + _GM_FIXTURE_BODY.replace(b"<ubFaceIndex>5</ubFaceIndex>", b"<ubFaceIndex>7</ubFaceIndex>")
                          .replace(b"<usVoiceIndex>5</usVoiceIndex>", b"<usVoiceIndex>7</usVoiceIndex>")
        + b"    <GrowthModifierWisdom>9</GrowthModifierWisdom>\n"
        b"  </PROFILE>\n"
        b"</MERCPROFILES>\n"
    )
    # The only growth spelling on disk is prefix-less → normalization leaves it
    # under the field name (nothing to rename) and the value is readable.
    norm = profiles_xml.normalize_profile_tags(profiles_xml.read_slot(xml, 7))
    assert norm["GrowthModifierWisdom"] == "9"

    profiles_xml.upsert(
        xml, Merc(uiIndex=7, ubFaceIndex=7, zName="Stale", zNickname="St",
                  GrowthModifierWisdom=40)
    )
    block = _find_profile(xml, 7)
    assert block is not None
    bnode = block.find("bGrowthModifierWisdom")
    assert bnode is not None and bnode.text == "40", (
        "self-heal failed: edit not written to the engine's b-prefixed tag"
    )
    assert block.find("GrowthModifierWisdom") is None, "stale prefix-less duplicate left behind"


# ──────────────────────────────────────────────────────────────────────────
#  AIMAvailability.xml
# ──────────────────────────────────────────────────────────────────────────

def test_aim_upsert_creates_file_if_missing(tmp_path: Path, sample_aim_binding: AimBinding) -> None:
    xml = tmp_path / "AIMAvailability.xml"
    aim_availability.upsert(xml, sample_aim_binding)
    contents = xml.read_text()
    assert "<ProfilId>220</ProfilId>" in contents  # note: single-L typo IS canonical
    assert "<AimBioID>52</AimBioID>" in contents


def test_aim_read_all(tmp_path: Path, sample_aim_binding: AimBinding) -> None:
    xml = tmp_path / "AIMAvailability.xml"
    aim_availability.upsert(xml, sample_aim_binding)
    all_bindings = aim_availability.read_all(xml)
    assert 220 in all_bindings
    assert all_bindings[220].AimBioID == 52


def test_aim_remove(tmp_path: Path, sample_aim_binding: AimBinding) -> None:
    xml = tmp_path / "AIMAvailability.xml"
    aim_availability.upsert(xml, sample_aim_binding)
    assert aim_availability.remove(xml, 220) is True
    assert 220 not in aim_availability.read_all(xml)


def test_canonical_aim_bio_id_lookups() -> None:
    """The canonical mapping for known scattered slots (from Appendix B)."""
    table = aim_availability.CANONICAL_AIM_BIO_IDS
    # Vanilla AIM
    assert table[0] == 0
    assert table[39] == 39
    # 1.13 expanded AIM linear group
    assert table[170] == 40
    assert table[175] == 45  # the bug-fix test slot
    assert table[177] == 47
    assert table[186] == 69
    assert table[187] == 70
    # Scattered (from Appendix B)
    assert table[215] == 17
    assert table[230] == 48
    assert table[235] == 56  # Leech, out-of-order
    assert table[251] == 63  # Smoke, last in vanilla


def test_compute_fresh_aim_bio_id_for_new_slot(tmp_path: Path) -> None:
    """Slot 200 isn't in the canonical table — we assign the lowest free ID."""
    xml = tmp_path / "AIMAvailability.xml"  # doesn't exist yet
    # All canonical IDs 0..70 are "reserved" by the table
    fresh = aim_availability.compute_aim_bio_id(xml, ui_index=200)
    assert fresh >= 71  # first free after the canonical mappings


def test_compute_existing_aim_bio_id_returns_table_value(tmp_path: Path) -> None:
    """For slot 175, return 45 (the canonical value)."""
    xml = tmp_path / "AIMAvailability.xml"
    assert aim_availability.compute_aim_bio_id(xml, ui_index=175) == 45


# ──────────────────────────────────────────────────────────────────────────
#  MercAvailability.xml — the M.E.R.C.-website equivalent
# ──────────────────────────────────────────────────────────────────────────

def test_merc_upsert_creates_file_if_missing(tmp_path: Path, sample_merc_binding: MercBinding) -> None:
    xml = tmp_path / "MercAvailability.xml"
    merc_availability.upsert(xml, sample_merc_binding)
    contents = xml.read_text()
    assert "<ProfilId>198</ProfilId>" in contents
    assert "<MercBioID>42</MercBioID>" in contents
    assert "<Name>Eskimo</Name>" in contents


def test_merc_read_all_keyed_by_profil_id(tmp_path: Path, sample_merc_binding: MercBinding) -> None:
    """MercAvailability rows are keyed by ProfilId (the slot pointer), not uiIndex."""
    xml = tmp_path / "MercAvailability.xml"
    merc_availability.upsert(xml, sample_merc_binding)
    all_bindings = merc_availability.read_all(xml)
    assert 198 in all_bindings
    assert all_bindings[198].MercBioID == 42


def test_merc_remove(tmp_path: Path, sample_merc_binding: MercBinding) -> None:
    xml = tmp_path / "MercAvailability.xml"
    merc_availability.upsert(xml, sample_merc_binding)
    assert merc_availability.remove(xml, 198) is True
    assert 198 not in merc_availability.read_all(xml)


def test_canonical_merc_bio_id_lookups() -> None:
    """Vanilla MERC 40-50 maps linearly to MercBioID 0-10."""
    table = merc_availability.CANONICAL_MERC_BIO_IDS
    assert table[40] == 0       # Biff
    assert table[45] == 5       # Larry/Cougar
    assert table[50] == 10      # Larry Roachburn
    # No canonical entry for the expansion range — those are mod-allocated
    assert 178 not in table
    assert 198 not in table


def test_compute_fresh_merc_bio_id_for_new_slot(tmp_path: Path) -> None:
    """Slot 200 isn't in the canonical table — assign the lowest free ID."""
    xml = tmp_path / "MercAvailability.xml"
    fresh = merc_availability.compute_merc_bio_id(xml, profil_id=200)
    assert fresh >= 11  # first free after vanilla 0-10


def test_compute_existing_merc_bio_id_returns_canonical(tmp_path: Path) -> None:
    """For vanilla MERC slot 45, return 5 (the canonical value, 45 - 40)."""
    xml = tmp_path / "MercAvailability.xml"
    assert merc_availability.compute_merc_bio_id(xml, profil_id=45) == 5


def test_lookup_skips_placeholder_minus_one_rows(tmp_path: Path) -> None:
    """A modded MercAvailability with `<MercBioID>-1</MercBioID>` rows must
    be treated as unbound, not as a real binding pointing at offset -1.

    Same defensive-handling as lookup_aim_bio_id — writing a bio at
    offset (-1 × 1120) = -1120 would corrupt the start of MERCBIOS.EDT.
    """
    xml = tmp_path / "MercAvailability.xml"
    placeholder = MercBinding(
        uiIndex=99, Name="placeholder", ProfilId=199, MercBioID=0,
    )
    merc_availability.upsert(xml, placeholder)
    # Hand-rewrite to inject the -1 sentinel that modded files use
    text = xml.read_text()
    text = text.replace("<MercBioID>0</MercBioID>", "<MercBioID>-1</MercBioID>")
    xml.write_text(text)
    # Placeholder ProfilId remains 199; lookup must return None (treat as unbound)
    assert merc_availability.lookup_merc_bio_id(xml, profil_id=199) is None


# ──────────────────────────────────────────────────────────────────────────
#  MercStartingGear.xml
# ──────────────────────────────────────────────────────────────────────────

def test_gear_upsert_writes_block(tmp_path: Path, sample_gear: Gear) -> None:
    xml = tmp_path / "MercStartingGear.xml"
    starting_gear.upsert(xml, sample_gear)
    text = xml.read_text()
    assert "<mIndex>220</mIndex>" in text
    assert "<mWeapon>2</mWeapon>" in text
    assert "<mAbsolutePrice>-1</mAbsolutePrice>" in text


def test_gear_refuses_absolute_price_not_minus_one() -> None:
    """The canonical rule: model validator prevents constructing such a Gear."""
    with pytest.raises(ValidationError):
        Gear(mIndex=220, kits=[GearKit(mAbsolutePrice=0)])


def test_gear_round_trip(tmp_path: Path, sample_gear: Gear) -> None:
    xml = tmp_path / "MercStartingGear.xml"
    starting_gear.upsert(xml, sample_gear)
    read = starting_gear.read_slot(xml, 220)
    assert read is not None
    assert read.mIndex == 220
    assert len(read.kits) == 1
    assert read.kits[0].mWeapon == 2
    assert read.kits[0].mAbsolutePrice == -1


def test_gear_clear_slot(tmp_path: Path, sample_gear: Gear) -> None:
    xml = tmp_path / "MercStartingGear.xml"
    starting_gear.upsert(xml, sample_gear)
    assert starting_gear.clear_slot(xml, 220) is True
    assert starting_gear.read_slot(xml, 220) is None


# ──────────────────────────────────────────────────────────────────────────
#  Encoding safety (cp1252 / mislabeled XML) — parse_tolerant +
#  save_atomic_preserving. Before this, the four core writers re-serialized
#  to utf-8 WITHOUT a declaration (mojibaking a cp1252 file's sibling rows
#  for the engine, which reads a decl-less file in its own codepage), and a
#  raw cp1252 high byte under a utf-8/absent declaration raised
#  XMLSyntaxError that rolled back and HARD-BLOCKED every save. The engine's
#  expat (XML_ParserCreate(NULL)) decodes only UTF-8/UTF-16/ISO-8859-1/ASCII
#  and re-reads char data as CP_UTF8, so the writers normalize ALL output to
#  self-describing UTF-8; the parse side rescues legacy bytes into the tree.
# ──────────────────────────────────────────────────────────────────────────
import re as _re

from mercwizard_core.inject import _atomic_xml


def test_parse_tolerant_rescues_cp1252_highbyte(tmp_path: Path) -> None:
    """A decl-less file carrying a raw cp1252 high byte (0xE9 = é) parses
    instead of raising XMLSyntaxError — the rescue that un-bricks save."""
    p = tmp_path / "legacy.xml"
    p.write_bytes("<R><z>Renée</z></R>".encode("cp1252"))  # 0xE9, no decl
    tree = _atomic_xml.parse_tolerant(p)
    assert tree.findtext(".//z") == "Renée"


def test_save_preserving_always_utf8_with_decl(tmp_path: Path) -> None:
    """save_atomic_preserving normalizes to UTF-8 + a utf-8 declaration —
    never echoes a source Windows-1252 codepage (engine-fatal) — and the
    transcoded accented text is valid UTF-8 (0xE9 → 0xC3 0xA9)."""
    p = tmp_path / "legacy.xml"
    p.write_bytes("<?xml version='1.0' encoding='Windows-1252'?>\n<R><z>Renée</z></R>".encode("cp1252"))
    tree = _atomic_xml.parse_tolerant(p)
    out = tmp_path / "out.xml"
    _atomic_xml.save_atomic_preserving(tree, out)
    raw = out.read_bytes()
    raw.decode("utf-8")                       # valid utf-8 (raises otherwise)
    head = raw[:80].lower()
    assert b"<?xml" in head and b"utf-8" in head and b"1252" not in head
    assert b"\xc3\xa9" in raw and b"\xe9" not in raw
    # Round-trips back through the tolerant parser with the value intact.
    assert _atomic_xml.parse_tolerant(out).findtext(".//z") == "Renée"


def test_profiles_survives_cp1252_nodecl_highbyte(tmp_path: Path, sample_merc: Merc) -> None:
    """Upserting onto a legacy decl-less file with a raw cp1252 sibling no
    longer hard-blocks; the sibling value survives, output is valid UTF-8."""
    xml = tmp_path / "MercProfiles.xml"
    legacy = ("<MERCPROFILES><PROFILE><uiIndex>5</uiIndex>"
              "<zNickname>Renée</zNickname></PROFILE></MERCPROFILES>")
    xml.write_bytes(legacy.encode("cp1252"))   # 0xE9, no declaration
    profiles_xml.upsert(xml, sample_merc)       # must NOT raise
    assert profiles_xml.read_slot(xml, 5)["zNickname"] == "Renée"
    assert profiles_xml.read_slot(xml, 220) is not None
    raw = xml.read_bytes()
    raw.decode("utf-8")
    assert b"\xc3\xa9" in raw and b"\xe9" not in raw


def test_profiles_cp1252_declared_normalizes_to_utf8(tmp_path: Path, sample_merc: Merc) -> None:
    """A correctly cp1252-DECLARED file (which lxml parses fine) is
    re-written as UTF-8, never echoed back as Windows-1252."""
    xml = tmp_path / "MercProfiles.xml"
    legacy = ("<?xml version='1.0' encoding='Windows-1252'?>\n<MERCPROFILES>"
              "<PROFILE><uiIndex>5</uiIndex><zNickname>Renée</zNickname>"
              "</PROFILE></MERCPROFILES>")
    xml.write_bytes(legacy.encode("cp1252"))
    profiles_xml.upsert(xml, sample_merc)
    assert profiles_xml.read_slot(xml, 5)["zNickname"] == "Renée"
    head = xml.read_bytes()[:80].lower()
    assert b"<?xml" in head and b"utf-8" in head and b"1252" not in head


def test_profiles_output_has_xml_declaration(tmp_path: Path, sample_merc: Merc) -> None:
    """The writer restores the <?xml?> declaration (the old save dropped it,
    a latent regression vs the declaration MercProfiles.xml ships with)."""
    xml = tmp_path / "MercProfiles.xml"
    profiles_xml.upsert(xml, sample_merc)
    head = xml.read_bytes()[:80].lower()
    assert head.startswith(b"<?xml") and b"utf-8" in head


def _corrupt_to_cp1252_nodecl(xml: Path) -> None:
    """Turn a writer-authored file into a legacy decl-less file carrying a
    raw cp1252 high byte (in a comment), reproducing the BUG-2 parse-block
    shape independent of the file's schema."""
    text = xml.read_text(encoding="utf-8")
    text = _re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", text, count=1)  # drop decl
    text = text.replace(">", "><!-- café -->", 1)                # inject é after root open
    xml.write_bytes(text.encode("cp1252"))                       # raw 0xE9


def test_aim_survives_cp1252_highbyte(tmp_path: Path, sample_aim_binding: AimBinding) -> None:
    xml = tmp_path / "AIMAvailability.xml"
    aim_availability.upsert(xml, sample_aim_binding)
    _corrupt_to_cp1252_nodecl(xml)
    aim_availability.upsert(xml, sample_aim_binding)   # must NOT raise
    raw = xml.read_bytes()
    raw.decode("utf-8")
    assert b"\xe9" not in raw


def test_merc_avail_survives_cp1252_highbyte(tmp_path: Path, sample_merc_binding: MercBinding) -> None:
    xml = tmp_path / "MercAvailability.xml"
    merc_availability.upsert(xml, sample_merc_binding)
    _corrupt_to_cp1252_nodecl(xml)
    merc_availability.upsert(xml, sample_merc_binding)  # must NOT raise
    raw = xml.read_bytes()
    raw.decode("utf-8")
    assert b"\xe9" not in raw


def test_gear_survives_cp1252_highbyte(tmp_path: Path, sample_gear: Gear) -> None:
    xml = tmp_path / "MercStartingGear.xml"
    starting_gear.upsert(xml, sample_gear)
    _corrupt_to_cp1252_nodecl(xml)
    starting_gear.upsert(xml, sample_gear)             # must NOT raise
    raw = xml.read_bytes()
    raw.decode("utf-8")
    assert b"\xe9" not in raw
