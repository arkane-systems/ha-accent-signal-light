"""Tests for SignalBaseLight -- the base-layer light entity."""

from __future__ import annotations

from homeassistant.components.light import ColorMode
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.signal_light.const import (
    ATTR_ACCENT_NAME,
    ATTR_PRIORITY,
    ATTR_SIGNAL_NAME,
    ATTR_UNDERLYING_ENTITY_ID,
    DATA_COORDINATOR,
    DOMAIN,
    SERVICE_CLEAR_ACCENT,
    SERVICE_SET_ACCENT,
    SERVICE_SET_SIGNAL,
)
from custom_components.signal_light.light import SignalBaseLight

from .conftest import UNDERLYING_ENTITY_ID, FakeCoordinator


def _make_entity(fake_coordinator: FakeCoordinator, fake_config_entry) -> SignalBaseLight:
    return SignalBaseLight(fake_coordinator, fake_config_entry)


def _base_entity_id(hass, entry) -> str | None:
    registry = er.async_get(hass)
    return registry.async_get_entity_id("light", DOMAIN, f"{entry.entry_id}_base")


# ── Pure property tests (FakeCoordinator, no real hass) ──────────────────────


def test_state_properties_proxy_coordinator(fake_coordinator, fake_config_entry) -> None:
    fake_coordinator.base_on = True
    fake_coordinator.base_attrs = {
        "brightness": 100,
        "hs_color": (10.0, 20.0),
        "effect": "rainbow",
    }
    entity = _make_entity(fake_coordinator, fake_config_entry)

    assert entity.is_on is True
    assert entity.brightness == 100
    assert entity.hs_color == (10.0, 20.0)
    assert entity.effect == "rainbow"


def test_extra_state_attributes_exposes_underlying_entity_id(
    fake_coordinator, fake_config_entry
) -> None:
    entity = _make_entity(fake_coordinator, fake_config_entry)

    assert entity.extra_state_attributes == {
        ATTR_UNDERLYING_ENTITY_ID: UNDERLYING_ENTITY_ID,
    }


def test_color_mode_infers_from_hs_color(fake_coordinator, fake_config_entry) -> None:
    entity = _make_entity(fake_coordinator, fake_config_entry)
    entity._attr_supported_color_modes = {ColorMode.HS, ColorMode.ONOFF}
    fake_coordinator.base_attrs = {"hs_color": (10.0, 20.0)}

    assert entity.color_mode == ColorMode.HS


def test_color_mode_falls_back_when_unsupported(fake_coordinator, fake_config_entry) -> None:
    entity = _make_entity(fake_coordinator, fake_config_entry)
    entity._attr_supported_color_modes = {ColorMode.ONOFF}
    fake_coordinator.base_attrs = {"hs_color": (10.0, 20.0)}

    assert entity.color_mode == ColorMode.ONOFF


def test_color_mode_defaults_onoff_with_no_attrs(fake_coordinator, fake_config_entry) -> None:
    entity = _make_entity(fake_coordinator, fake_config_entry)
    entity._attr_supported_color_modes = {ColorMode.ONOFF}
    fake_coordinator.base_attrs = {}

    assert entity.color_mode == ColorMode.ONOFF


# ── Capability-mirroring tests ───────────────────────────────────────────────


def test_sync_capabilities_from_underlying_reads_effect_and_kelvin(
    fake_coordinator, fake_config_entry
) -> None:
    fake_coordinator.underlying_state = State(
        UNDERLYING_ENTITY_ID,
        "on",
        {
            "supported_color_modes": ["hs"],
            "supported_features": 44,
            "effect_list": ["rainbow", "colorloop"],
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6500,
        },
    )
    entity = _make_entity(fake_coordinator, fake_config_entry)

    assert entity.effect_list == ["rainbow", "colorloop"]
    assert entity.min_color_temp_kelvin == 2000
    assert entity.max_color_temp_kelvin == 6500
    assert int(entity.supported_features) == 44


def test_sync_capabilities_from_underlying_handles_missing_entity(
    fake_coordinator, fake_config_entry
) -> None:
    fake_coordinator.underlying_state = None
    entity = _make_entity(fake_coordinator, fake_config_entry)

    assert entity.effect_list is None
    assert entity.min_color_temp_kelvin is None
    assert entity.max_color_temp_kelvin is None
    assert int(entity.supported_features) == 0


def test_sync_supported_color_modes_filters_invalid_strings(
    fake_coordinator, fake_config_entry
) -> None:
    entity = _make_entity(fake_coordinator, fake_config_entry)
    state = State(UNDERLYING_ENTITY_ID, "on", {"supported_color_modes": ["bogus_mode"]})

    entity._sync_supported_color_modes_from_underlying(state)

    assert entity._attr_supported_color_modes == {ColorMode.ONOFF}


def test_sync_supported_color_modes_handles_onoff_brightness_coexistence(
    fake_coordinator, fake_config_entry
) -> None:
    entity = _make_entity(fake_coordinator, fake_config_entry)
    state = State(
        UNDERLYING_ENTITY_ID,
        "on",
        {"supported_color_modes": ["onoff", "brightness", "hs"]},
    )

    entity._sync_supported_color_modes_from_underlying(state)

    assert entity._attr_supported_color_modes == {ColorMode.HS}


# ── RestoreEntity lifecycle (needs real hass) ────────────────────────────────


async def test_async_added_to_hass_no_last_state(hass, setup_integration) -> None:
    entity_id = _base_entity_id(hass, setup_integration)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state.state == "off"


async def test_async_added_to_hass_restores_on_state(
    hass, mock_config_entry, light_service_calls
) -> None:
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = _base_entity_id(hass, mock_config_entry)
    assert entity_id is not None

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_restore_cache(
        hass,
        [State(entity_id, "on", {"brightness": 150, "hs_color": (10.0, 20.0)})],
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["brightness"] == 150


async def test_async_added_to_hass_restores_off_state(
    hass, mock_config_entry, light_service_calls
) -> None:
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = _base_entity_id(hass, mock_config_entry)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_restore_cache(hass, [State(entity_id, "off", {})])

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "off"


# ── turn_on/turn_off and entity-service forwarding (needs real hass) ────────


async def test_turn_on_forwards_to_coordinator(hass, setup_integration) -> None:
    entity_id = _base_entity_id(hass, setup_integration)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "brightness": 200}, blocking=True
    )

    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["brightness"] == 200


async def test_turn_off_forwards_to_coordinator(hass, setup_integration) -> None:
    entity_id = _base_entity_id(hass, setup_integration)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id}, blocking=True
    )

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": entity_id}, blocking=True
    )

    assert hass.states.get(entity_id).state == "off"


async def test_set_signal_service_call(hass, setup_integration) -> None:
    entity_id = _base_entity_id(hass, setup_integration)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SIGNAL,
        {ATTR_SIGNAL_NAME: "alarm", ATTR_PRIORITY: 100, "brightness": 200},
        target={"entity_id": entity_id},
        blocking=True,
    )

    coordinator = hass.data[DOMAIN][setup_integration.entry_id][DATA_COORDINATOR]
    assert coordinator.active_signal["name"] == "alarm"
    assert coordinator.active_signal["attrs"]["brightness"] == 200


async def test_clear_accent_service_call(hass, setup_integration) -> None:
    entity_id = _base_entity_id(hass, setup_integration)
    coordinator = hass.data[DOMAIN][setup_integration.entry_id][DATA_COORDINATOR]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_ACCENT,
        {ATTR_ACCENT_NAME: "mood", ATTR_PRIORITY: 50, "brightness": 120},
        target={"entity_id": entity_id},
        blocking=True,
    )
    assert coordinator.active_accent is not None

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CLEAR_ACCENT,
        {ATTR_ACCENT_NAME: "mood"},
        target={"entity_id": entity_id},
        blocking=True,
    )
    assert coordinator.active_accent is None
