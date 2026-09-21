"""Base-layer light entity for the Accent & Signal Light integration.

The *base layer* is the lowest-priority tier of the three-layer model.  It is
exposed to Home Assistant as a standard ``light`` entity so that users,
automations, and other integrations can set the "default" state of the
accent/signal light using all of the normal ``light.turn_on`` / ``light.turn_off``
service calls.

Design notes
------------
* The entity's **reported state** reflects the base layer — i.e. what the user
  has configured as the default — rather than the actual state of the physical
  light.  This is intentional: when a higher-priority accent or signal is
  active, the physical light is controlled by that layer, but the base layer
  entity still shows what the base layer *would* display if the accent/signal
  were removed.

* The entity uses :class:`homeassistant.components.light.RestoreLight` so that
  the last-known base-layer state survives a HA restart.

* The four domain services (``set_signal``, ``clear_signal``, ``set_accent``,
  ``clear_accent``) are registered here as *entity services* so that HA
  automatically routes each call to the correct coordinator via the
  ``entity_id`` the caller supplies.

* Supported colour modes are declared as the full set of standard modes.  The
  coordinator forwards whatever attributes the user supplies directly to the
  underlying ``light.turn_on`` call, so the actual capability is constrained
  only by the physical light.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_COLOR_NAME,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
    valid_supported_color_modes,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
import homeassistant.helpers.config_validation as cv

from .const import (
    ATTR_ACCENT_NAME,
    ATTR_PRIORITY,
    ATTR_SIGNAL_NAME,
    ATTR_UNDERLYING_ENTITY_ID,
    DATA_COORDINATOR,
    DEFAULT_PRIORITY,
    DOMAIN,
    SERVICE_CLEAR_ACCENT,
    SERVICE_CLEAR_SIGNAL,
    SERVICE_SET_ACCENT,
    SERVICE_SET_SIGNAL,
)
from .coordinator import SignalLightCoordinator

_LOGGER = logging.getLogger(__name__)

# ── Light-attribute schema reused for entity-service schemas ──────────────────

_LIGHT_ATTR_SCHEMA = {
    vol.Optional(ATTR_BRIGHTNESS):        vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
    vol.Optional(ATTR_BRIGHTNESS_PCT):    vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional(ATTR_COLOR_NAME):        cv.string,
    vol.Optional(ATTR_COLOR_TEMP_KELVIN): vol.All(vol.Coerce(int), vol.Range(min=1000, max=10000)),
    vol.Optional(ATTR_HS_COLOR): vol.All(
        list,
        vol.ExactSequence([
            vol.All(vol.Coerce(float), vol.Range(min=0, max=360)),
            vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        ]),
    ),
    vol.Optional(ATTR_RGB_COLOR): vol.All(
        list,
        vol.ExactSequence([
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        ]),
    ),
    vol.Optional(ATTR_RGBW_COLOR): vol.All(
        list,
        vol.ExactSequence([
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        ]),
    ),
    vol.Optional(ATTR_RGBWW_COLOR): vol.All(
        list,
        vol.ExactSequence([
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        ]),
    ),
    vol.Optional(ATTR_XY_COLOR): vol.All(
        list,
        vol.ExactSequence([
            vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
            vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        ]),
    ),
    vol.Optional(ATTR_EFFECT):     cv.string,
    vol.Optional(ATTR_TRANSITION): vol.All(vol.Coerce(float), vol.Range(min=0)),
}

# ── Platform setup ─────────────────────────────────────────────────────────────


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the base-layer light entity for this config entry.

    Also registers the four domain services as entity services on this
    platform so that HA routes ``signal_light.*`` calls to the correct
    entity (and thereby the correct coordinator) based on ``entity_id``.

    Args:
        hass:              The Home Assistant instance.
        config_entry:      The config entry being set up.
        async_add_entities: Callback to register new entities with HA.
    """
    coordinator: SignalLightCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        DATA_COORDINATOR
    ]

    # Create and register the base-layer light entity.
    entity = SignalBaseLight(coordinator, config_entry)
    async_add_entities([entity])

    # ── Register entity services ───────────────────────────────────────────────
    # Entity services are registered once per platform; HA handles routing the
    # service call to the correct entity instance based on entity_id/device_id.

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_SET_SIGNAL,
        {
            vol.Required(ATTR_SIGNAL_NAME): cv.string,
            vol.Optional(ATTR_PRIORITY, default=DEFAULT_PRIORITY): vol.All(
                vol.Coerce(int), vol.Range(min=1)
            ),
            **_LIGHT_ATTR_SCHEMA,
        },
        "async_handle_set_signal",
    )

    platform.async_register_entity_service(
        SERVICE_CLEAR_SIGNAL,
        {
            vol.Required(ATTR_SIGNAL_NAME): cv.string,
        },
        "async_handle_clear_signal",
    )

    platform.async_register_entity_service(
        SERVICE_SET_ACCENT,
        {
            vol.Required(ATTR_ACCENT_NAME): cv.string,
            vol.Optional(ATTR_PRIORITY, default=DEFAULT_PRIORITY): vol.All(
                vol.Coerce(int), vol.Range(min=1)
            ),
            **_LIGHT_ATTR_SCHEMA,
        },
        "async_handle_set_accent",
    )

    platform.async_register_entity_service(
        SERVICE_CLEAR_ACCENT,
        {
            vol.Required(ATTR_ACCENT_NAME): cv.string,
        },
        "async_handle_clear_accent",
    )


# ── Entity class ───────────────────────────────────────────────────────────────


class SignalBaseLight(LightEntity, RestoreEntity):
    """HA light entity representing the base layer of a signal-light instance.

    This entity is the primary interaction point for setting the "default"
    state of the lights.  It also hosts the signal/accent service handlers so
    that HA can route service calls to the correct coordinator.

    State reporting
    ~~~~~~~~~~~~~~~
    The entity always reports the *base layer* state, not the physical light
    state.  This means ``is_on`` / ``brightness`` / ``hs_color`` etc. reflect
    what the user last set via the base layer, regardless of whether a
    higher-priority accent or signal is currently overriding the physical light.

    The entity's friendly name defaults to the config-entry title (which the
    user set during onboarding), suffixed with " Base".
    """

    _attr_should_poll = False  # Push-based; updates arrive via coordinator listener.

    def __init__(
        self,
        coordinator: SignalLightCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialise the entity.

        Args:
            coordinator:  The coordinator managing this instance's layers.
            config_entry: The config entry for this instance.
        """
        self.coordinator = coordinator
        self._config_entry = config_entry

        # Unique ID for the base-layer entity — based on the entry so it
        # survives entity_id renames.
        self._attr_unique_id = f"{config_entry.entry_id}_base"

        # Human-readable name shown in the UI.
        self._attr_name = f"{config_entry.title} Base"

        # Group all entities for this instance under one device.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.title,
            manufacturer="Accent & Signal Light",
            model="Accent & Signal Light Controller",
            entry_type=None,
        )

        # De-registration callable returned by async_add_listener.
        self._remove_listener: Any = None

        # Mirrored capability metadata from the underlying light entity.
        self._underlying_effect_list: list[str] | None = None
        self._underlying_min_color_temp_kelvin: int | None = None
        self._underlying_max_color_temp_kelvin: int | None = None

        # Must be set before the entity is added; HA validates capabilities
        # during add_entity, before async_added_to_hass runs.
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_supported_features = LightEntityFeature(0)
        self._sync_capabilities_from_underlying()

    def _sync_capabilities_from_underlying(self) -> None:
        """Mirror capability metadata from the underlying light when available."""
        underlying_entity = self.coordinator.hass.states.get(
            self.coordinator.underlying_entity_id
        )
        self._sync_supported_color_modes_from_underlying(underlying_entity)

        if underlying_entity is None:
            self._attr_supported_features = LightEntityFeature(0)
            self._underlying_effect_list = None
            self._underlying_min_color_temp_kelvin = None
            self._underlying_max_color_temp_kelvin = None
            return

        self._attr_supported_features = LightEntityFeature(
            underlying_entity.attributes.get("supported_features", 0)
        )
        raw_effect_list = underlying_entity.attributes.get("effect_list")
        self._underlying_effect_list = (
            [str(effect) for effect in raw_effect_list]
            if isinstance(raw_effect_list, list)
            else None
        )
        self._underlying_min_color_temp_kelvin = underlying_entity.attributes.get(
            "min_color_temp_kelvin"
        )
        self._underlying_max_color_temp_kelvin = underlying_entity.attributes.get(
            "max_color_temp_kelvin"
        )

    def _sync_supported_color_modes_from_underlying(self, underlying_entity) -> None:
        """Mirror supported color modes from the underlying light when available."""
        if underlying_entity is None:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            return

        raw_modes = underlying_entity.attributes.get("supported_color_modes")
        if not raw_modes:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            return

        try:
            modes = {ColorMode(mode) for mode in raw_modes}
        except ValueError:
            _LOGGER.warning(
                "Could not parse supported_color_modes from %s: %s",
                self.coordinator.underlying_entity_id,
                raw_modes,
            )
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            return

        # Some integrations may expose combinations HA rejects. Normalize toward
        # modern semantics where ONOFF/BRIGHTNESS are standalone modes.
        if len(modes) > 1:
            modes.discard(ColorMode.ONOFF)
            modes.discard(ColorMode.BRIGHTNESS)

        if not modes:
            modes = {ColorMode.ONOFF}

        try:
            valid_supported_color_modes(modes)
        except (HomeAssistantError, ValueError):
            _LOGGER.warning(
                "Underlying light %s exposed invalid supported_color_modes %s; "
                "falling back to on/off.",
                self.coordinator.underlying_entity_id,
                sorted(str(mode) for mode in modes),
            )
            modes = {ColorMode.ONOFF}

        self._attr_supported_color_modes = modes

    # ── HA lifecycle ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Called when the entity is added to HA.

        1. Restores the last-known base-layer state (if available).
        2. Registers a listener with the coordinator so we're notified of
           external state changes (accent/signal layers changing).
        """
        # Restore state from the previous run.
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unavailable", "unknown"):
            was_on = last_state.state == "on"
            attrs: dict[str, Any] = {}

            # Extract whichever colour attributes were stored.
            for attr in (
                ATTR_BRIGHTNESS,
                ATTR_COLOR_TEMP_KELVIN,
                ATTR_HS_COLOR,
                ATTR_RGB_COLOR,
                ATTR_RGBW_COLOR,
                ATTR_RGBWW_COLOR,
                ATTR_XY_COLOR,
                ATTR_EFFECT,
            ):
                if (val := last_state.attributes.get(attr)) is not None:
                    attrs[attr] = val

            if was_on:
                await self.coordinator.async_set_base_on(attrs)
            else:
                await self.coordinator.async_set_base_off()

        # Subscribe to coordinator updates.
        self._remove_listener = self.coordinator.async_add_listener(
            self._async_handle_coordinator_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Called when the entity is being removed from HA.

        De-registers the coordinator listener to prevent memory leaks.
        """
        if self._remove_listener is not None:
            self._remove_listener()

    @callback
    def _async_handle_coordinator_update(self) -> None:
        """Called by the coordinator when any layer changes.

        Schedules a state write so HA sees the updated entity state.
        """
        self._sync_capabilities_from_underlying()
        self.async_write_ha_state()

    # ── LightEntity state properties ──────────────────────────────────────────

    @property
    def is_on(self) -> bool:
        """Return True if the base layer is on."""
        return self.coordinator.base_on

    @property
    def brightness(self) -> int | None:
        """Return the base layer's brightness (0-255), or None."""
        return self.coordinator.base_attrs.get(ATTR_BRIGHTNESS)

    @property
    def color_mode(self) -> ColorMode | str | None:
        """Return the active colour mode of the base layer.

        The colour mode is inferred from whichever colour attribute is set.
        Falls back to :attr:`~homeassistant.components.light.ColorMode.ONOFF`
        when no colour info has been set.
        """
        attrs = self.coordinator.base_attrs
        inferred = ColorMode.ONOFF
        if ATTR_HS_COLOR in attrs:
            inferred = ColorMode.HS
        elif ATTR_RGB_COLOR in attrs:
            inferred = ColorMode.RGB
        elif ATTR_RGBW_COLOR in attrs:
            inferred = ColorMode.RGBW
        elif ATTR_RGBWW_COLOR in attrs:
            inferred = ColorMode.RGBWW
        elif ATTR_XY_COLOR in attrs:
            inferred = ColorMode.XY
        elif ATTR_COLOR_TEMP_KELVIN in attrs:
            inferred = ColorMode.COLOR_TEMP
        elif ATTR_BRIGHTNESS in attrs:
            inferred = ColorMode.BRIGHTNESS

        supported = self.supported_color_modes or {ColorMode.ONOFF}
        if inferred in supported:
            return inferred
        if ColorMode.BRIGHTNESS in supported and ATTR_BRIGHTNESS in attrs:
            return ColorMode.BRIGHTNESS
        if ColorMode.ONOFF in supported:
            return ColorMode.ONOFF
        return next(iter(supported))

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the base layer HS colour, or None."""
        return self.coordinator.base_attrs.get(ATTR_HS_COLOR)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the base layer RGB colour, or None."""
        return self.coordinator.base_attrs.get(ATTR_RGB_COLOR)

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return the base layer RGBW colour, or None."""
        return self.coordinator.base_attrs.get(ATTR_RGBW_COLOR)

    @property
    def rgbww_color(self) -> tuple[int, int, int, int, int] | None:
        """Return the base layer RGBWW colour, or None."""
        return self.coordinator.base_attrs.get(ATTR_RGBWW_COLOR)

    @property
    def xy_color(self) -> tuple[float, float] | None:
        """Return the base layer XY colour, or None."""
        return self.coordinator.base_attrs.get(ATTR_XY_COLOR)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the base layer colour temperature in Kelvin, or None."""
        return self.coordinator.base_attrs.get(ATTR_COLOR_TEMP_KELVIN)

    @property
    def effect(self) -> str | None:
        """Return the base layer effect name, or None."""
        return self.coordinator.base_attrs.get(ATTR_EFFECT)

    @property
    def effect_list(self) -> list[str] | None:
        """Return available effects from the underlying light, if any."""
        return self._underlying_effect_list

    @property
    def min_color_temp_kelvin(self) -> int | None:
        """Return minimum supported color temperature (Kelvin), if available."""
        return self._underlying_min_color_temp_kelvin

    @property
    def max_color_temp_kelvin(self) -> int | None:
        """Return maximum supported color temperature (Kelvin), if available."""
        return self._underlying_max_color_temp_kelvin

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional diagnostic attributes for the base layer."""
        return {
            ATTR_UNDERLYING_ENTITY_ID: self.coordinator.underlying_entity_id,
        }

    # ── LightEntity turn on/off ────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the base layer on.

        All standard ``light.turn_on`` keyword arguments (brightness, colour,
        effect, transition, …) are accepted and forwarded to the coordinator.

        Args:
            **kwargs: Light attribute keyword arguments.
        """
        _LOGGER.debug("Base layer turn_on: %s", kwargs)
        await self.coordinator.async_set_base_on(kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the base layer off.

        Args:
            **kwargs: Accepted for API compatibility (e.g. ``transition``);
                      forwarded to the coordinator for bookkeeping.
        """
        _LOGGER.debug("Base layer turn_off")
        await self.coordinator.async_set_base_off()

    # ── Entity-service handlers ────────────────────────────────────────────────
    # These methods are called by HA's entity-service routing when a caller
    # targets this entity with one of the four signal_light.* services.

    async def async_handle_set_signal(self, **kwargs: Any) -> None:
        """Handle the ``signal_light.set_signal`` entity service call.

        Extracts the signal name, priority, and any light attributes from
        *kwargs* and forwards them to the coordinator.

        Args:
            **kwargs: Fields as defined in the entity-service schema
                      (signal_name, priority, optional light attrs).
        """
        signal_name: str = kwargs.pop(ATTR_SIGNAL_NAME)
        priority: int = kwargs.pop(ATTR_PRIORITY, DEFAULT_PRIORITY)
        # Remaining kwargs are the light attributes.
        await self.coordinator.async_set_signal(signal_name, priority, kwargs)

    async def async_handle_clear_signal(self, **kwargs: Any) -> None:
        """Handle the ``signal_light.clear_signal`` entity service call.

        Args:
            **kwargs: Fields as defined in the entity-service schema
                      (signal_name).
        """
        signal_name: str = kwargs[ATTR_SIGNAL_NAME]
        await self.coordinator.async_clear_signal(signal_name)

    async def async_handle_set_accent(self, **kwargs: Any) -> None:
        """Handle the ``signal_light.set_accent`` entity service call.

        Extracts the accent name, priority, and any light attributes from
        *kwargs* and forwards them to the coordinator.

        Args:
            **kwargs: Fields as defined in the entity-service schema
                      (accent_name, priority, optional light attrs).
        """
        accent_name: str = kwargs.pop(ATTR_ACCENT_NAME)
        priority: int = kwargs.pop(ATTR_PRIORITY, DEFAULT_PRIORITY)
        await self.coordinator.async_set_accent(accent_name, priority, kwargs)

    async def async_handle_clear_accent(self, **kwargs: Any) -> None:
        """Handle the ``signal_light.clear_accent`` entity service call.

        Args:
            **kwargs: Fields as defined in the entity-service schema
                      (accent_name).
        """
        accent_name: str = kwargs[ATTR_ACCENT_NAME]
        await self.coordinator.async_clear_accent(accent_name)
