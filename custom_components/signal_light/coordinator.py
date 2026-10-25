"""Coordinator for the Accent & Signal Light integration.

This module implements :class:`SignalLightCoordinator`, which is the central
object for a single signal-light *instance* (one config entry).  It owns the
three-layer light model and is responsible for:

* Keeping the state of each layer in memory.
* Computing the *effective* state by evaluating the layers top-down.
* Applying the effective state to the underlying physical light entity by
  calling the standard ``light.turn_on`` / ``light.turn_off`` HA services.
* Notifying registered listener callbacks whenever any layer changes so that
  the associated HA entities can schedule a state refresh.

Layer hierarchy (highest priority first)
-----------------------------------------
1. **Signal layer** — a priority-sorted list of named signals.  The signal with
   the highest priority controls the light.  When the base layer is off, only
   signals above the configured wake threshold may turn the physical light on.
   When a signal is canceled the next highest-priority signal takes over; if
   the queue is empty, control falls through to the accent layer.

2. **Accent layer** — a priority-sorted list of named accents.  The accent with
   the highest priority controls the light when no signal is active and the
   base layer is on.  When it is removed the next highest-priority accent takes
   over; if the stack is empty, control falls through to the base layer.

3. **Base layer** — the default light state.  This is the state that was most
   recently applied via the base-layer :class:`~signal_light.light.SignalBaseLight`
   entity (``light.turn_on`` / ``light.turn_off``).

Each accent/signal entry is stored as a plain dict::

    {
        "name":     str,          # unique identifier within this instance
        "priority": int,          # higher = takes precedence
        "attrs":    dict,         # kwargs forwarded to light.turn_on
    }

Entries are kept sorted by ``priority`` descending, so index 0 is always the
active (highest-priority) entry.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.components.light import (
    ATTR_COLOR_NAME,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_SIGNAL_WAKE_PRIORITY,
    CONF_UNDERLYING_ENTITY_ID,
    DEFAULT_SIGNAL_WAKE_PRIORITY,
)

_LOGGER = logging.getLogger(__name__)

_COLOR_DESCRIPTOR_ATTRS = (
    ATTR_COLOR_NAME,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_XY_COLOR,
    ATTR_COLOR_TEMP_KELVIN,
)


class SignalLightCoordinator:
    """Central coordinator for one signal-light instance.

    All mutable state lives here.  HA entities (light, sensors) hold a
    reference to the coordinator and register a listener callback; the
    coordinator calls those callbacks after every state change so the entities
    can schedule a write-state to HA.
    """

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialise the coordinator.

        Args:
            hass:         The Home Assistant instance.
            config_entry: The config entry for this signal-light instance.
        """
        self.hass = hass
        self.config_entry = config_entry

        # Entity ID of the underlying physical light we control.
        self.underlying_entity_id: str = config_entry.data[CONF_UNDERLYING_ENTITY_ID]

        # Callbacks registered by HA entities.  Each callback is called
        # (with no arguments) whenever any layer changes.
        self._listeners: list[Callable[[], None]] = []

        # ── Base layer ────────────────────────────────────────────────────────
        # True when the base layer is "on".
        self._base_on: bool = False
        # Light attributes last set on the base layer (brightness, color, etc.).
        self._base_attrs: dict[str, Any] = {}

        # ── Accent layer ──────────────────────────────────────────────────────
        # Priority-sorted list (highest priority first).  Each element is a
        # dict with keys "name", "priority", "attrs".
        self._accent_stack: list[dict[str, Any]] = []

        # ── Signal layer ──────────────────────────────────────────────────────
        # Priority-sorted list (highest priority first).  Same structure as
        # accent stack.
        self._signal_queue: list[dict[str, Any]] = []

        # Re-apply effective state when the underlying light becomes available.
        self._remove_underlying_listener = async_track_state_change_event(
            hass,
            [self.underlying_entity_id],
            self._async_handle_underlying_state_change,
        )

    # =========================================================================
    # Listener management
    # =========================================================================

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register *listener* to be called on every state change.

        Returns a de-registration callable — call it to stop receiving updates.
        """
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass  # already removed

        return _remove

    @callback
    def _async_notify_listeners(self) -> None:
        """Invoke every registered listener callback."""
        for listener in self._listeners:
            listener()

    async def async_shutdown(self) -> None:
        """Release listeners owned by the coordinator."""
        if self._remove_underlying_listener is not None:
            self._remove_underlying_listener()
            self._remove_underlying_listener = None

    @callback
    def _async_underlying_available(self) -> bool:
        """Return True when the underlying light entity is available."""
        state = self.hass.states.get(self.underlying_entity_id)
        return bool(state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN))

    @callback
    def _async_handle_underlying_state_change(self, event) -> None:
        """Re-apply effective state when the underlying light becomes available."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        old_available = bool(
            old_state and old_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        )
        new_available = bool(
            new_state and new_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        )

        # Keep entities in sync with changing underlying capabilities/availability.
        self._async_notify_listeners()

        if not old_available and new_available:
            self.hass.async_create_task(self._async_apply_state())

    # =========================================================================
    # Base-layer access
    # =========================================================================

    @property
    def base_on(self) -> bool:
        """Whether the base layer is currently on."""
        return self._base_on

    @property
    def base_attrs(self) -> dict[str, Any]:
        """A copy of the base layer's current light attributes."""
        return dict(self._base_attrs)

    @property
    def signal_wake_priority(self) -> int:
        """Return the wake threshold used while the base layer is off."""
        return int(
            self.config_entry.data.get(
                CONF_SIGNAL_WAKE_PRIORITY, DEFAULT_SIGNAL_WAKE_PRIORITY
            )
        )

    async def async_set_base_on(
        self, attrs: dict[str, Any], transition: float | None = None
    ) -> None:
        """Turn the base layer on with *attrs* as light parameters.

        *attrs* is merged into the existing base-layer attributes so that
        ``turn_on`` calls that only update brightness (for example) still
        preserve the previously-set colour.

        Args:
            attrs:      Light attribute kwargs (brightness, hs_color, …) as
                        would be passed to ``light.turn_on``.
            transition: Fade time in seconds for the physical light's change
                        in response to this command.  It is not stored with
                        the base layer, so later re-applications of the base
                        layer do not replay it.
        """
        attrs = dict(attrs)
        self._normalize_attrs(attrs)

        self._base_on = True
        if any(attr in attrs for attr in _COLOR_DESCRIPTOR_ATTRS):
            for attr in _COLOR_DESCRIPTOR_ATTRS:
                self._base_attrs.pop(attr, None)
            # A manually-chosen colour implicitly cancels any active effect,
            # unless the caller explicitly set a new effect in the same call.
            if ATTR_EFFECT not in attrs:
                self._base_attrs.pop(ATTR_EFFECT, None)
        self._base_attrs.update(attrs)
        _LOGGER.debug("Base layer on: %s", self._base_attrs)
        await self._async_apply_state(transition)
        self._async_notify_listeners()

    async def async_set_base_off(self, transition: float | None = None) -> None:
        """Turn the base layer off.

        The stored attributes are *not* cleared so that re-enabling the base
        layer restores the last-set colour.

        Args:
            transition: Fade time in seconds for the physical light's change
                        in response to this command.
        """
        self._base_on = False
        _LOGGER.debug("Base layer off")
        await self._async_apply_state(transition)
        self._async_notify_listeners()

    # =========================================================================
    # Accent-layer access
    # =========================================================================

    @property
    def accent_stack(self) -> list[dict[str, Any]]:
        """A copy of the accent stack, sorted by priority (highest first)."""
        return list(self._accent_stack)

    @property
    def active_accent(self) -> dict[str, Any] | None:
        """The currently-active accent entry, or *None* if the stack is empty."""
        return self._accent_stack[0] if self._accent_stack else None

    async def async_set_accent(
        self, name: str, priority: int, attrs: dict[str, Any]
    ) -> None:
        """Add or update a named accent in the stack.

        If an accent with *name* already exists it is replaced (so the caller
        can update priority or attributes without a separate clear).  The stack
        is then re-sorted so the highest-priority entry is always first.

        Args:
            name:     Unique name for this accent within the instance.
            priority: Integer priority; higher values take precedence.
            attrs:    Light attribute kwargs forwarded to ``light.turn_on``.
        """
        attrs = dict(attrs)
        self._normalize_attrs(attrs)

        # Replace existing entry with the same name, if any.
        self._accent_stack = [e for e in self._accent_stack if e["name"] != name]
        self._accent_stack.append({"name": name, "priority": priority, "attrs": attrs})
        # Keep sorted — highest priority first.
        self._accent_stack.sort(key=lambda e: e["priority"], reverse=True)

        _LOGGER.debug(
            "Accent set: name=%r priority=%d — stack: %s",
            name, priority, [e["name"] for e in self._accent_stack],
        )
        await self._async_apply_state()
        self._async_notify_listeners()

    async def async_clear_accent(self, name: str) -> None:
        """Remove the named accent from the stack.

        If no accent with *name* exists, this is a no-op.

        Args:
            name: The name of the accent to remove.
        """
        prev_active = self.active_accent
        self._accent_stack = [e for e in self._accent_stack if e["name"] != name]

        _LOGGER.debug(
            "Accent cleared: name=%r — stack: %s",
            name, [e["name"] for e in self._accent_stack],
        )

        # Only push a light update when the *active* accent changed.
        if prev_active != self.active_accent:
            await self._async_apply_state()
        self._async_notify_listeners()

    # =========================================================================
    # Signal-layer access
    # =========================================================================

    @property
    def signal_queue(self) -> list[dict[str, Any]]:
        """A copy of the signal queue, sorted by priority (highest first)."""
        return list(self._signal_queue)

    @property
    def active_signal(self) -> dict[str, Any] | None:
        """The currently-active signal entry, or *None* if the queue is empty."""
        return self._signal_queue[0] if self._signal_queue else None

    async def async_set_signal(
        self, name: str, priority: int, attrs: dict[str, Any]
    ) -> None:
        """Add or update a named signal in the queue.

        If a signal with *name* already exists it is replaced.  The queue is
        then re-sorted so the highest-priority entry is always first.

        Args:
            name:     Unique name for this signal within the instance.
            priority: Integer priority; higher values take precedence.
            attrs:    Light attribute kwargs forwarded to ``light.turn_on``.
        """
        attrs = dict(attrs)
        self._normalize_attrs(attrs)

        # Replace existing entry with the same name, if any.
        self._signal_queue = [e for e in self._signal_queue if e["name"] != name]
        self._signal_queue.append({"name": name, "priority": priority, "attrs": attrs})
        # Keep sorted — highest priority first.
        self._signal_queue.sort(key=lambda e: e["priority"], reverse=True)

        _LOGGER.debug(
            "Signal set: name=%r priority=%d — queue: %s",
            name, priority, [e["name"] for e in self._signal_queue],
        )
        await self._async_apply_state()
        self._async_notify_listeners()

    async def async_clear_signal(self, name: str) -> None:
        """Remove the named signal from the queue.

        If no signal with *name* exists, this is a no-op.  When the active
        (highest-priority) signal is removed, the next signal in the queue
        becomes active automatically.

        Args:
            name: The name of the signal to remove.
        """
        prev_active = self.active_signal
        self._signal_queue = [e for e in self._signal_queue if e["name"] != name]

        _LOGGER.debug(
            "Signal cleared: name=%r — queue: %s",
            name, [e["name"] for e in self._signal_queue],
        )

        # Only push a light update when the *active* signal changed.
        if prev_active != self.active_signal:
            await self._async_apply_state()
        self._async_notify_listeners()

    # =========================================================================
    # Effective-state computation and physical-light control
    # =========================================================================

    def get_effective_state(self) -> tuple[bool, dict[str, Any]]:
        """Compute the effective light state by evaluating layers top-down.

        Evaluation order:

        1. Signal queue  — highest-priority signal's attrs when the base layer
           is on, or when that signal's priority exceeds the wake threshold.
        2. Accent stack  — highest-priority accent's attrs when the base layer
           is on.
        3. Base layer    — ``(_base_on, _base_attrs)``.

        Returns:
            A ``(on, attrs)`` tuple where *on* is ``True`` when the light
            should be on, and *attrs* is the dict of light parameters.
        """
        if self._signal_queue:
            active_signal = self._signal_queue[0]
            if self._base_on or active_signal["priority"] > self.signal_wake_priority:
                return True, dict(active_signal["attrs"])

        if self._base_on and self._accent_stack:
            return True, dict(self._accent_stack[0]["attrs"])

        # Fall back to the base layer.
        return self._base_on, dict(self._base_attrs)

    @staticmethod
    def _normalize_attrs(attrs: dict[str, Any]) -> None:
        """Normalize light attrs to avoid conflicting color descriptors."""
        present_color_attrs = [key for key in attrs if key in _COLOR_DESCRIPTOR_ATTRS]
        if len(present_color_attrs) <= 1:
            return

        keep_attr = present_color_attrs[-1]
        for attr in _COLOR_DESCRIPTOR_ATTRS:
            if attr != keep_attr:
                attrs.pop(attr, None)

        _LOGGER.warning(
            "Received multiple color descriptors %s; keeping %s",
            present_color_attrs,
            keep_attr,
        )

    async def _async_apply_state(self, transition: float | None = None) -> None:
        """Push the current effective state to the underlying physical light.

        Called internally after every mutation.  Translates the effective state
        into either a ``light.turn_on`` or ``light.turn_off`` service call on
        :attr:`underlying_entity_id`.

        Args:
            transition: A fade time, in seconds, requested by the command that
                        caused this apply.  It applies to this call only and
                        takes precedence over any ``transition`` carried by
                        the effective layer's own attributes.  ``None`` leaves
                        the layer's own attributes (if any) untouched; ``0``
                        is a real request for no fade.
        """
        if not self._async_underlying_available():
            _LOGGER.debug(
                "Skipping apply; underlying light %s is not available",
                self.underlying_entity_id,
            )
            return

        on, attrs = self.get_effective_state()

        if on:
            service_data: dict[str, Any] = {"entity_id": self.underlying_entity_id}
            service_data.update(attrs)
            if transition is not None:
                service_data[ATTR_TRANSITION] = transition
            _LOGGER.debug(
                "Applying to %s: turn_on %s", self.underlying_entity_id, attrs
            )
            await self.hass.services.async_call(
                "light", "turn_on", service_data, blocking=True
            )
        else:
            service_data = {"entity_id": self.underlying_entity_id}
            if transition is not None:
                service_data[ATTR_TRANSITION] = transition
            _LOGGER.debug(
                "Applying to %s: turn_off %s", self.underlying_entity_id, service_data
            )
            await self.hass.services.async_call(
                "light", "turn_off", service_data, blocking=True
            )
