"""Sensor entities for the Accent & Signal Light integration.

This module provides four read-only sensor entities that give visibility into
the current state of the accent stack and signal queue for a given
signal-light instance.  All four sensors share the same device as the
base-layer light entity so they appear together in the HA device page.

Sensor summary
--------------
``active_signal``
    State: the name of the highest-priority signal currently in the queue, or
    ``"none"`` when the queue is empty.
    Attributes: ``signal_wake_priority`` (the configured wake threshold, always
    present) plus the full set of light attributes for the active signal
    (only present when a signal is active).

``active_accent``
    State: the name of the highest-priority accent currently on the stack, or
    ``"none"`` when the stack is empty.
    Attributes: the full set of light attributes for the active accent (empty
    dict when no accent is active).

``signal_queue``
    State: integer count of signals currently in the queue.
    Attributes: ``queue`` — a list of dicts, each with ``name``, ``priority``,
    and ``attrs`` keys, ordered highest-priority first.

``accent_stack``
    State: integer count of accents currently on the stack.
    Attributes: ``stack`` — a list of dicts, each with ``name``, ``priority``,
    and ``attrs`` keys, ordered highest-priority first.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_ATTRS,
    ATTR_NAME,
    ATTR_PRIORITY,
    ATTR_SIGNAL_WAKE_PRIORITY,
    DATA_COORDINATOR,
    DOMAIN,
    SENSOR_ACCENT_STACK,
    SENSOR_ACTIVE_ACCENT,
    SENSOR_ACTIVE_SIGNAL,
    SENSOR_SIGNAL_QUEUE,
)
from .coordinator import SignalLightCoordinator

_LOGGER = logging.getLogger(__name__)


# ── Platform setup ─────────────────────────────────────────────────────────────


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register all four sensor entities for this config entry.

    Args:
        hass:               The Home Assistant instance.
        config_entry:       The config entry being set up.
        async_add_entities: Callback to register new entities with HA.
    """
    coordinator: SignalLightCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        DATA_COORDINATOR
    ]

    async_add_entities(
        [
            ActiveSignalSensor(coordinator, config_entry),
            ActiveAccentSensor(coordinator, config_entry),
            SignalQueueSensor(coordinator, config_entry),
            AccentStackSensor(coordinator, config_entry),
        ]
    )


# ── Base class ─────────────────────────────────────────────────────────────────


class _SignalLightSensorBase(SensorEntity):
    """Common base for all Accent & Signal Light sensors.

    Handles coordinator subscription/unsubscription and device info, so
    subclasses only need to implement the state-specific properties.
    """

    _attr_should_poll = False  # Push-based.

    def __init__(
        self,
        coordinator: SignalLightCoordinator,
        config_entry: ConfigEntry,
        unique_id_suffix: str,
        name_suffix: str,
    ) -> None:
        """Initialise the sensor.

        Args:
            coordinator:    The coordinator for this instance.
            config_entry:   The config entry for this instance.
            unique_id_suffix: Appended to the entry_id to form the unique_id.
            name_suffix:    Appended to the entry title to form the entity name.
        """
        self.coordinator = coordinator
        self._config_entry = config_entry

        self._attr_unique_id = f"{config_entry.entry_id}_{unique_id_suffix}"
        self._attr_name = f"{config_entry.title} {name_suffix}"

        # All sensor entities share the same device as the base-layer light.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.title,
            manufacturer="Accent & Signal Light",
            model="Accent & Signal Light Controller",
        )

        self._remove_listener: Any = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates when added to HA."""
        self._remove_listener = self.coordinator.async_add_listener(
            self._async_handle_coordinator_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from coordinator updates when removed from HA."""
        if self._remove_listener is not None:
            self._remove_listener()

    @callback
    def _async_handle_coordinator_update(self) -> None:
        """Trigger a state write whenever the coordinator reports a change."""
        self.async_write_ha_state()


# ── Concrete sensor implementations ───────────────────────────────────────────


class ActiveSignalSensor(_SignalLightSensorBase):
    """Sensor: name of the currently-active (highest-priority) signal.

    State is ``"none"`` when no signal is queued.  The extra state attributes
    expose the full light-attribute dict of the active signal.
    """

    def __init__(
        self, coordinator: SignalLightCoordinator, config_entry: ConfigEntry
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            unique_id_suffix=SENSOR_ACTIVE_SIGNAL,
            name_suffix="Active Signal",
        )
        self._attr_icon = "mdi:alert-circle"

    @property
    def native_value(self) -> str:
        """Return the name of the active signal, or 'none'."""
        active = self.coordinator.active_signal
        return active[ATTR_NAME] if active is not None else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the light attributes of the active signal, plus the wake threshold."""
        attrs: dict[str, Any] = {
            ATTR_SIGNAL_WAKE_PRIORITY: self.coordinator.signal_wake_priority,
        }
        active = self.coordinator.active_signal
        if active is not None:
            attrs[ATTR_PRIORITY] = active[ATTR_PRIORITY]
            attrs.update(active[ATTR_ATTRS])
        return attrs


class ActiveAccentSensor(_SignalLightSensorBase):
    """Sensor: name of the currently-active (highest-priority) accent.

    State is ``"none"`` when no accent is stacked.  The extra state attributes
    expose the full light-attribute dict of the active accent.
    """

    def __init__(
        self, coordinator: SignalLightCoordinator, config_entry: ConfigEntry
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            unique_id_suffix=SENSOR_ACTIVE_ACCENT,
            name_suffix="Active Accent",
        )
        self._attr_icon = "mdi:palette"

    @property
    def native_value(self) -> str:
        """Return the name of the active accent, or 'none'."""
        active = self.coordinator.active_accent
        return active[ATTR_NAME] if active is not None else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the light attributes of the active accent."""
        active = self.coordinator.active_accent
        if active is None:
            return {}
        return {
            ATTR_PRIORITY: active[ATTR_PRIORITY],
            **active[ATTR_ATTRS],
        }


class SignalQueueSensor(_SignalLightSensorBase):
    """Sensor: count of signals currently in the priority queue.

    State is an integer count.  The ``queue`` extra attribute contains the
    full ordered list of signal entries (name, priority, attrs).
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "signals"

    def __init__(
        self, coordinator: SignalLightCoordinator, config_entry: ConfigEntry
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            unique_id_suffix=SENSOR_SIGNAL_QUEUE,
            name_suffix="Signal Queue",
        )
        self._attr_icon = "mdi:bell-ring"

    @property
    def native_value(self) -> int:
        """Return the number of signals currently queued."""
        return len(self.coordinator.signal_queue)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full signal queue as a list of dicts."""
        return {
            "queue": [
                {
                    ATTR_NAME:     entry[ATTR_NAME],
                    ATTR_PRIORITY: entry[ATTR_PRIORITY],
                    ATTR_ATTRS:    entry[ATTR_ATTRS],
                }
                for entry in self.coordinator.signal_queue
            ]
        }


class AccentStackSensor(_SignalLightSensorBase):
    """Sensor: count of accents currently on the priority stack.

    State is an integer count.  The ``stack`` extra attribute contains the
    full ordered list of accent entries (name, priority, attrs).
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "accents"

    def __init__(
        self, coordinator: SignalLightCoordinator, config_entry: ConfigEntry
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            unique_id_suffix=SENSOR_ACCENT_STACK,
            name_suffix="Accent Stack",
        )
        self._attr_icon = "mdi:layers"

    @property
    def native_value(self) -> int:
        """Return the number of accents currently on the stack."""
        return len(self.coordinator.accent_stack)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full accent stack as a list of dicts."""
        return {
            "stack": [
                {
                    ATTR_NAME:     entry[ATTR_NAME],
                    ATTR_PRIORITY: entry[ATTR_PRIORITY],
                    ATTR_ATTRS:    entry[ATTR_ATTRS],
                }
                for entry in self.coordinator.accent_stack
            ]
        }
