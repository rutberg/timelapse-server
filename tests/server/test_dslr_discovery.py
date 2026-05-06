from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.dslr_discovery import (
    parse_body_info,
    parse_list_all_config,
    parse_vendor,
    propose_property_map,
)


FIXTURES = Path(__file__).parent / "fixtures" / "dslr"


def load_fixture(name: str) -> dict:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    # Drop the `_source` documentation key — it's not a real path.
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def setting(prop, field):
    """Find a setting dropdown by `settings_field` in a proposal dict."""
    for d in prop["setting_dropdowns"]:
        if d["settings_field"] == field:
            return d
    return None


def init_key(prop, field):
    for k in prop["init_keys"]:
        if k["settings_field"] == field:
            return k
    return None


def tile(prop, field):
    for t in prop["telemetry_tiles"]:
        if t["field"] == field:
            return t
    return None


class TestParseVendor:
    def test_canon_from_manufacturer(self):
        assert parse_vendor("Canon Inc.", "Canon EOS R6") == "Canon"

    def test_nikon_from_manufacturer(self):
        assert parse_vendor("Nikon Corporation", "D3400") == "Nikon"

    def test_sony_from_manufacturer(self):
        assert parse_vendor("Sony Corporation", "ILCE-7M4") == "Sony"

    def test_falls_back_to_model_when_manufacturer_missing(self):
        assert parse_vendor(None, "Sony ILCE-7M4") == "Sony"

    def test_returns_none_for_unknown(self):
        assert parse_vendor("Phase One", "IQ4 150MP") is None


class TestParseBodyInfo:
    def test_canon_r6(self):
        info = parse_body_info(load_fixture("canon-eos-r6.json"))
        assert info["vendor"] == "Canon"
        assert info["model"] == "Canon EOS R6"
        # Canon's eosserialnumber takes priority over generic serialnumber.
        assert info["serial"] == "012345678901"

    def test_nikon_d3400(self):
        info = parse_body_info(load_fixture("nikon-d3400.json"))
        assert info["vendor"] == "Nikon"
        assert info["model"] == "D3400"
        assert info["serial"] == "2123456"

    def test_sony_a7m4(self):
        info = parse_body_info(load_fixture("sony-a7m4.json"))
        assert info["vendor"] == "Sony"
        assert info["model"] == "ILCE-7M4"


class TestProposeCanonR6:
    @pytest.fixture
    def prop(self):
        return propose_property_map(
            load_fixture("canon-eos-r6.json"),
            discovered_at="2026-05-06T00:00:00Z",
        )

    def test_body_vendor_is_canon(self, prop):
        assert prop["body"]["vendor"] == "Canon"

    def test_telemetry_tiles_include_canon_specific(self, prop):
        # Canon exposes battery, available shots, lens — Nikon/Sony don't.
        fields = [t["field"] for t in prop["telemetry_tiles"]]
        assert "battery" in fields
        assert "available_shots" in fields
        assert "lens_name" in fields
        assert "exposure_mode" in fields
        assert "camera_model" in fields

    def test_exposure_mode_uses_canon_key(self, prop):
        t = tile(prop, "exposure_mode")
        assert t["read_key"] == "autoexposuremode"

    def test_aperture_uses_canon_key(self, prop):
        d = setting(prop, "aperture")
        assert d["read_key"] == "aperture"
        assert d["write_key"] == "aperture"

    def test_image_format_uses_canon_key(self, prop):
        d = setting(prop, "image_format")
        assert d["read_key"] == "imageformat"

    def test_drive_mode_uses_canon_key(self, prop):
        k = init_key(prop, "drive_mode")
        assert k["read_key"] == "drivemode"

    def test_shutterspeed_read_eq_write_on_canon(self, prop):
        d = setting(prop, "shutterspeed")
        assert d["read_key"] == "shutterspeed"
        # On Canon, since `shutterspeed` is writable, the prefer-Nikon order in
        # the priority table is fine: the `(shutterspeed, shutterspeed2)` pair
        # only matches when `shutterspeed2` exists too. Canon doesn't expose
        # shutterspeed2, so we fall through to the (shutterspeed, shutterspeed)
        # pair.
        assert d["write_key"] == "shutterspeed"


class TestProposeNikonD3400:
    @pytest.fixture
    def prop(self):
        return propose_property_map(
            load_fixture("nikon-d3400.json"),
            discovered_at="2026-05-06T00:00:00Z",
        )

    def test_body_vendor_is_nikon(self, prop):
        assert prop["body"]["vendor"] == "Nikon"

    def test_no_canon_specific_telemetry_tiles(self, prop):
        # Nikon D3400 doesn't expose availableshots / shuttercounter / lensname
        # under /main/status/.
        fields = [t["field"] for t in prop["telemetry_tiles"]]
        assert "available_shots" not in fields
        assert "shutter_counter" not in fields
        assert "lens_name" not in fields

    def test_battery_and_camera_present(self, prop):
        fields = [t["field"] for t in prop["telemetry_tiles"]]
        assert "battery" in fields
        assert "camera_model" in fields

    def test_exposure_mode_uses_expprogram(self, prop):
        t = tile(prop, "exposure_mode")
        assert t["read_key"] == "expprogram"

    def test_aperture_uses_f_number(self, prop):
        d = setting(prop, "aperture")
        assert d["read_key"] == "f-number"
        assert d["write_key"] == "f-number"

    def test_image_format_uses_imagequality(self, prop):
        d = setting(prop, "image_format")
        assert d["read_key"] == "imagequality"

    def test_drive_mode_uses_capturemode(self, prop):
        k = init_key(prop, "drive_mode")
        assert k["read_key"] == "capturemode"

    def test_shutterspeed_read_differs_from_write_on_nikon(self, prop):
        # The Nikon-specific quirk: writable name is shutterspeed2.
        d = setting(prop, "shutterspeed")
        assert d["read_key"] == "shutterspeed"
        assert d["write_key"] == "shutterspeed2"


class TestProposeSonyA7M4:
    @pytest.fixture
    def prop(self):
        return propose_property_map(
            load_fixture("sony-a7m4.json"),
            discovered_at="2026-05-06T00:00:00Z",
        )

    def test_body_vendor_is_sony(self, prop):
        assert prop["body"]["vendor"] == "Sony"

    def test_no_battery_available_shots_lens_or_shutter_count(self, prop):
        # The whole point of #13: Sony bodies don't get permanent blank tiles
        # for telemetry they don't expose.
        fields = [t["field"] for t in prop["telemetry_tiles"]]
        assert "battery" not in fields
        assert "available_shots" not in fields
        assert "shutter_counter" not in fields
        assert "lens_name" not in fields

    def test_exposure_mode_and_camera_model_still_present(self, prop):
        fields = [t["field"] for t in prop["telemetry_tiles"]]
        assert "exposure_mode" in fields
        assert "camera_model" in fields

    def test_exposure_mode_uses_expprogram(self, prop):
        t = tile(prop, "exposure_mode")
        assert t["read_key"] == "expprogram"

    def test_aperture_uses_f_number_on_sony(self, prop):
        d = setting(prop, "aperture")
        assert d["read_key"] == "f-number"

    def test_image_format_uses_imagequality_on_sony(self, prop):
        d = setting(prop, "image_format")
        assert d["read_key"] == "imagequality"

    def test_drive_mode_uses_capturemode_on_sony(self, prop):
        k = init_key(prop, "drive_mode")
        assert k["read_key"] == "capturemode"

    def test_shutterspeed_read_eq_write_on_sony(self, prop):
        # Sony doesn't have the Nikon shutterspeed2 split — read==write.
        d = setting(prop, "shutterspeed")
        assert d["read_key"] == "shutterspeed"
        assert d["write_key"] == "shutterspeed"


class TestProposeUnknownVendor:
    def test_does_not_crash(self):
        prop = propose_property_map(
            load_fixture("unknown-vendor.json"),
            discovered_at="2026-05-06T00:00:00Z",
        )
        assert prop["body"]["vendor"] is None

    def test_universal_subset_is_present(self):
        # ISO + whitebalance + camera_model exist on virtually every PTP body.
        prop = propose_property_map(
            load_fixture("unknown-vendor.json"),
            discovered_at="2026-05-06T00:00:00Z",
        )
        setting_fields = [d["settings_field"] for d in prop["setting_dropdowns"]]
        tile_fields = [t["field"] for t in prop["telemetry_tiles"]]
        assert "iso" in setting_fields
        assert "whitebalance" in setting_fields
        assert "camera_model" in tile_fields

    def test_omits_settings_with_no_match(self):
        prop = propose_property_map(
            load_fixture("unknown-vendor.json"),
            discovered_at="2026-05-06T00:00:00Z",
        )
        setting_fields = [d["settings_field"] for d in prop["setting_dropdowns"]]
        # No shutterspeed / aperture / etc. entries in the fixture, so they
        # should not appear in the proposal.
        assert "shutterspeed" not in setting_fields
        assert "aperture" not in setting_fields


class TestParseListAllConfig:
    def test_parses_minimal_block(self):
        stdout = (
            "/main/imgsettings/iso\n"
            "Label: ISO Speed\n"
            "Readonly: 0\n"
            "Type: RADIO\n"
            "Current: 400\n"
            "Choice: 0 Auto\n"
            "Choice: 1 100\n"
            "Choice: 2 200\n"
            "Choice: 3 400\n"
            "END\n"
        )
        result = parse_list_all_config(stdout)
        assert "/main/imgsettings/iso" in result
        entry = result["/main/imgsettings/iso"]
        assert entry["label"] == "ISO Speed"
        assert entry["type"] == "RADIO"
        assert entry["readonly"] is False
        assert entry["current"] == "400"
        assert entry["choices"] == ["Auto", "100", "200", "400"]

    def test_parses_multiple_blocks(self):
        stdout = (
            "/main/status/batterylevel\n"
            "Label: Battery Level\nReadonly: 1\nType: TEXT\nCurrent: 75%\nEND\n"
            "/main/imgsettings/iso\n"
            "Label: ISO Speed\nReadonly: 0\nType: RADIO\nCurrent: 400\nChoice: 0 100\nEND\n"
        )
        result = parse_list_all_config(stdout)
        assert set(result) == {"/main/status/batterylevel", "/main/imgsettings/iso"}
        assert result["/main/status/batterylevel"]["readonly"] is True
        assert result["/main/imgsettings/iso"]["choices"] == ["100"]

    def test_handles_block_without_trailing_end(self):
        stdout = (
            "/main/status/cameramodel\n"
            "Label: Camera Model\nReadonly: 1\nType: TEXT\nCurrent: Canon EOS R6\n"
        )
        result = parse_list_all_config(stdout)
        assert result["/main/status/cameramodel"]["current"] == "Canon EOS R6"
