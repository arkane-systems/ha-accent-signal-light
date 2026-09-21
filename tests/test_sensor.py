"""Tests for the four Accent & Signal Light sensor entities."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.signal_light.const import (
    DATA_COORDINATOR,
    DEFAULT_SIGNAL_WAKE_PRIORITY,
    DOMAIN,
    SENSOR_ACTIVE_SIGNAL,
)
from custom_components.signal_light.sensor import (
    AccentStackSensor,
    ActiveAccentSensor,
    ActiveSignalSensor,
    SignalQueueSensor,
)

# ── Pure formatting tests (FakeCoordinator, no real hass) ────────────────────


def test_active_signal_sensor_reports_active_entry(fake_coordinator, fake_config_entry) -> None:
    fake_coordinator.active_signal = {
        "name": "alarm",
        "priority": 100,
        "attrs": {"brightness": 200},
    }
    sensor = ActiveSignalSensor(fake_coordinator, fake_config_entry)

    assert sensor.native_value == "alarm"
    assert sensor.extra_state_attributes == {
        "signal_wake_priority": DEFAULT_SIGNAL_WAKE_PRIORITY,
        "priority": 100,
        "brightness": 200,
    }


def test_active_signal_sensor_none_when_empty(fake_coordinator, fake_config_entry) -> None:
    sensor = ActiveSignalSensor(fake_coordinator, fake_config_entry)

    assert sensor.native_value == "none"
    assert sensor.extra_state_attributes == {
        "signal_wake_priority": DEFAULT_SIGNAL_WAKE_PRIORITY,
    }


def test_active_accent_sensor_reports_active_entry(fake_coordinator, fake_config_entry) -> None:
    fake_coordinator.active_accent = {
        "name": "mood",
        "priority": 50,
        "attrs": {"hs_color": (10.0, 20.0)},
    }
    sensor = ActiveAccentSensor(fake_coordinator, fake_config_entry)

    assert sensor.native_value == "mood"
    assert sensor.extra_state_attributes == {"priority": 50, "hs_color": (10.0, 20.0)}


def test_active_accent_sensor_none_when_empty(fake_coordinator, fake_config_entry) -> None:
    sensor = ActiveAccentSensor(fake_coordinator, fake_config_entry)

    assert sensor.native_value == "none"
    assert sensor.extra_state_attributes == {}


def test_signal_queue_sensor_reports_count_and_queue(fake_coordinator, fake_config_entry) -> None:
    fake_coordinator.signal_queue = [
        {"name": "high", "priority": 100, "attrs": {}},
        {"name": "low", "priority": 10, "attrs": {}},
    ]
    sensor = SignalQueueSensor(fake_coordinator, fake_config_entry)

    assert sensor.native_value == 2
    assert sensor.extra_state_attributes == {"queue": fake_coordinator.signal_queue}


def test_signal_queue_sensor_empty(fake_coordinator, fake_config_entry) -> None:
    sensor = SignalQueueSensor(fake_coordinator, fake_config_entry)

    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"queue": []}


def test_accent_stack_sensor_reports_count_and_stack(fake_coordinator, fake_config_entry) -> None:
    fake_coordinator.accent_stack = [{"name": "mood", "priority": 50, "attrs": {}}]
    sensor = AccentStackSensor(fake_coordinator, fake_config_entry)

    assert sensor.native_value == 1
    assert sensor.extra_state_attributes == {"stack": fake_coordinator.accent_stack}


def test_accent_stack_sensor_empty(fake_coordinator, fake_config_entry) -> None:
    sensor = AccentStackSensor(fake_coordinator, fake_config_entry)

    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"stack": []}


# ── Live update via coordinator listener (needs real hass) ──────────────────


async def test_sensor_updates_on_coordinator_listener_notification(
    hass, setup_integration
) -> None:
    entry = setup_integration
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{SENSOR_ACTIVE_SIGNAL}"
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "none"

    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_set_signal("alarm", 100, {})
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "alarm"
