"""Shared fixtures for the signal_light test suite.

Uses pytest-homeassistant-custom-component (phacc) to provide an in-memory
Home Assistant core for the duration of each test -- no real server, no
network, no physical devices involved.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.signal_light.const import (
    CONF_SIGNAL_WAKE_PRIORITY,
    CONF_UNDERLYING_ENTITY_ID,
    DEFAULT_SIGNAL_WAKE_PRIORITY,
    DOMAIN,
)
from custom_components.signal_light.coordinator import SignalLightCoordinator

pytest_plugins = ["pytest_homeassistant_custom_component.common"]

UNDERLYING_ENTITY_ID = "light.test_light"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Make the signal_light custom integration loadable in every test."""


@pytest.fixture
def mock_underlying_light(hass: HomeAssistant) -> str:
    """Register a fake underlying light entity that is on and available."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "light", "test", "unique_test_light", suggested_object_id="test_light"
    )
    hass.states.async_set(
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
    return UNDERLYING_ENTITY_ID


@pytest.fixture
def mock_config_entry(
    hass: HomeAssistant, mock_underlying_light: str
) -> MockConfigEntry:
    """A config entry pointed at the fake underlying light."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Signal Light",
        data={
            CONF_UNDERLYING_ENTITY_ID: mock_underlying_light,
            CONF_SIGNAL_WAKE_PRIORITY: 5000,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def light_service_calls(hass: HomeAssistant) -> dict[str, list[Any]]:
    """Fake `light.turn_on`/`light.turn_off` services; returns their call logs.

    The coordinator calls these low-level services directly on the
    underlying light -- registering fakes lets tests assert on exactly what
    was sent, without needing a real light integration set up.
    """
    return {
        "turn_on": async_mock_service(hass, "light", "turn_on"),
        "turn_off": async_mock_service(hass, "light", "turn_off"),
    }


@pytest.fixture
def coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    light_service_calls: dict[str, list[Any]],
) -> SignalLightCoordinator:
    """A coordinator wired to the fake underlying light.

    Constructed directly rather than via async_setup_entry, for tests that
    only care about layer logic and don't need the light/sensor entities.
    Depends on light_service_calls so `light.turn_on`/`turn_off` are always
    registered -- every coordinator mutation can trigger an apply.
    """
    return SignalLightCoordinator(hass, mock_config_entry)


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Fully set up the integration (coordinator + light + sensor entities)."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


class _FakeStates:
    """Stands in for `hass.states`, returning whatever the test configured."""

    def __init__(self, owner: "FakeCoordinator") -> None:
        self._owner = owner

    def get(self, entity_id: str) -> Any:
        return self._owner.underlying_state


class _FakeHass:
    """Stands in for `hass`, exposing just the `.states.get()` surface that
    light.py's capability-mirroring code touches."""

    def __init__(self, owner: "FakeCoordinator") -> None:
        self.states = _FakeStates(owner)


class FakeCoordinator:
    """A minimal stand-in for SignalLightCoordinator.

    Exposes only the read-only attributes that light.py/sensor.py entities
    consult, as plain settable attributes -- no real hass, no service calls,
    no listener plumbing required to exercise their property logic.
    """

    def __init__(self) -> None:
        self.underlying_entity_id = UNDERLYING_ENTITY_ID
        # Returned by self.hass.states.get(underlying_entity_id); set this to
        # a homeassistant.core.State to exercise capability-sync logic.
        self.underlying_state: Any = None
        self.hass = _FakeHass(self)
        self.base_on = False
        self.base_attrs: dict[str, Any] = {}
        self.signal_wake_priority = DEFAULT_SIGNAL_WAKE_PRIORITY
        self.active_signal: dict[str, Any] | None = None
        self.active_accent: dict[str, Any] | None = None
        self.signal_queue: list[dict[str, Any]] = []
        self.accent_stack: list[dict[str, Any]] = []
        self._listeners: list[Any] = []

    def async_add_listener(self, listener: Any):
        self._listeners.append(listener)

        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove


@pytest.fixture
def fake_coordinator() -> FakeCoordinator:
    """A bare-bones coordinator double for pure property/formatting tests."""
    return FakeCoordinator()


@pytest.fixture
def fake_config_entry() -> SimpleNamespace:
    """A minimal config-entry double exposing just entry_id/title.

    Enough for constructing entities directly (bypassing hass) in pure
    property tests -- SignalBaseLight/_SignalLightSensorBase only read
    entry_id and title in __init__.
    """
    return SimpleNamespace(entry_id="test_entry_id", title="Test Signal Light")
