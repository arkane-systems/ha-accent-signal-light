"""Config flow for the Accent & Signal Light integration.

Guides the user through adding a new instance via the HA integrations UI, and
through *reconfiguring* an existing instance later.  The flow collects three
pieces of information:

1. **Name** — a human-readable label for this instance (e.g. "Living Room
   Accent & Signal Light").  Defaults to the domain name; the user is free
   to change it.

2. **Underlying light entity** — the ``entity_id`` of the physical light (or
   light group) that this instance will control.

3. **Signal wake priority** — the minimum priority a signal must exceed to turn
   the light on while the base layer is off.

Validation
----------
* The supplied entity_id must belong to a currently-loaded entity in the
  ``light`` domain.
* Duplicate entries for the same underlying entity are rejected; only one
  instance may manage a given physical light.

Reconfiguration
----------------
``async_step_reconfigure`` lets the user change the underlying entity and/or
signal wake priority of an *existing* entry without deleting and recreating
it — deleting would generate a new ``entry_id`` and thus a new device/entity
set, breaking any automations, dashboards, or scripts that reference them.
It shares its form-building and validation logic with ``async_step_user`` via
``_async_step_form``, and finishes with
:meth:`~homeassistant.config_entries.ConfigFlow.async_update_reload_and_abort`
instead of :meth:`~homeassistant.config_entries.ConfigFlow.async_create_entry`,
which updates the entry's data in place and reloads it.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_SIGNAL_WAKE_PRIORITY,
    CONF_UNDERLYING_ENTITY_ID,
    DEFAULT_SIGNAL_WAKE_PRIORITY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# ── helpers ───────────────────────────────────────────────────────────────────


def _get_light_entity_ids(hass: HomeAssistant) -> list[str]:
    """Return the entity_ids of all currently-loaded light entities."""
    registry = er.async_get(hass)
    return sorted(
        entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == LIGHT_DOMAIN
    )


def _existing_underlying_ids(
    hass: HomeAssistant, *, exclude_entry_id: str | None = None
) -> set[str]:
    """Return the underlying entity_ids already claimed by existing entries.

    Args:
        hass:             The Home Assistant instance.
        exclude_entry_id: When reconfiguring an entry, its own current claim
            on its underlying entity should not count against it.
    """
    return {
        entry.data[CONF_UNDERLYING_ENTITY_ID]
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id != exclude_entry_id
    }


# ── Config flow ───────────────────────────────────────────────────────────────


class SignalLightConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Accent & Signal Light.

    Presents a single form that collects the name, underlying entity_id, and
    signal wake priority, then creates (``async_step_user``) or updates
    (``async_step_reconfigure``) the config entry.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step shown when the user clicks 'Add integration'."""
        return await self._async_step_form(user_input, reconfigure_entry=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguring an existing entry's underlying entity/wake priority."""
        return await self._async_step_form(
            user_input, reconfigure_entry=self._get_reconfigure_entry()
        )

    async def _async_step_form(
        self,
        user_input: dict[str, Any] | None,
        *,
        reconfigure_entry: ConfigEntry | None,
    ) -> ConfigFlowResult:
        """Render/validate the shared name + entity + wake-priority form.

        When *user_input* is ``None`` (first visit) the form is rendered,
        pre-filled from *reconfigure_entry* if one is being reconfigured.
        When *user_input* is provided (form submitted) validation is run and,
        if successful, the entry is created or updated in place.

        Args:
            user_input:         Data submitted by the user, or ``None`` on
                first render.
            reconfigure_entry:  The entry being reconfigured, or ``None`` when
                adding a new instance.

        Returns:
            A :class:`~homeassistant.data_entry_flow.FlowResult`.
        """
        errors: dict[str, str] = {}

        # Collect the entity_ids of available lights for the selector. If an
        # entry is being reconfigured, make sure its current choice is always
        # offered even if that entity is momentarily unavailable.
        light_entity_ids = _get_light_entity_ids(self.hass)
        if reconfigure_entry is not None:
            current_entity_id = reconfigure_entry.data[CONF_UNDERLYING_ENTITY_ID]
            if current_entity_id not in light_entity_ids:
                light_entity_ids = sorted([*light_entity_ids, current_entity_id])

        if user_input is not None:
            entity_id: str = user_input[CONF_UNDERLYING_ENTITY_ID]

            # ── Validate ──────────────────────────────────────────────────────

            # 1. The chosen entity must be a light.
            if entity_id not in light_entity_ids:
                errors[CONF_UNDERLYING_ENTITY_ID] = "entity_not_found"

            # 2. No other instance already controls this entity. The entry
            #    being reconfigured is excluded — keeping (or re-picking) its
            #    own current entity must not be flagged as a duplicate.
            elif entity_id in _existing_underlying_ids(
                self.hass,
                exclude_entry_id=(
                    reconfigure_entry.entry_id if reconfigure_entry else None
                ),
            ):
                errors[CONF_UNDERLYING_ENTITY_ID] = "already_configured"

            if not errors:
                title = user_input.get("name") or entity_id
                data = {
                    CONF_UNDERLYING_ENTITY_ID: entity_id,
                    CONF_SIGNAL_WAKE_PRIORITY: user_input[CONF_SIGNAL_WAKE_PRIORITY],
                }

                if reconfigure_entry is not None:
                    _LOGGER.debug(
                        "Reconfiguring Accent & Signal Light entry %s: name=%r entity=%r",
                        reconfigure_entry.entry_id, user_input.get("name"), entity_id,
                    )
                    # Keep unique_id (derived from the underlying entity) in
                    # sync so a future "add instance" flow still detects this
                    # entity as claimed.
                    return self.async_update_reload_and_abort(
                        reconfigure_entry,
                        title=title,
                        unique_id=entity_id,
                        data=data,
                    )

                # Derive a unique_id from the underlying entity so that HA can
                # detect if the user accidentally tries to add the same
                # physical light a second time.
                await self.async_set_unique_id(entity_id)
                self._abort_if_unique_id_configured()

                _LOGGER.debug(
                    "Creating Accent & Signal Light entry: name=%r entity=%r",
                    user_input.get("name"), entity_id,
                )
                return self.async_create_entry(title=title, data=data)

        # ── Build the form schema ─────────────────────────────────────────────
        # Offer a friendly name field and a selector restricted to known lights.
        name_default = reconfigure_entry.title if reconfigure_entry else ""
        wake_priority_default = (
            # Entries created before signal_wake_priority existed never
            # stored it; fall back the same way the coordinator's own
            # signal_wake_priority property does.
            reconfigure_entry.data.get(
                CONF_SIGNAL_WAKE_PRIORITY, DEFAULT_SIGNAL_WAKE_PRIORITY
            )
            if reconfigure_entry
            else DEFAULT_SIGNAL_WAKE_PRIORITY
        )
        entity_id_default = (
            reconfigure_entry.data[CONF_UNDERLYING_ENTITY_ID]
            if reconfigure_entry
            else vol.UNDEFINED
        )
        schema = vol.Schema(
            {
                vol.Optional("name", default=name_default): cv.string,
                vol.Optional(
                    CONF_SIGNAL_WAKE_PRIORITY,
                    default=wake_priority_default,
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Required(
                    CONF_UNDERLYING_ENTITY_ID, default=entity_id_default
                ): vol.In(light_entity_ids) if light_entity_ids else cv.string,
            }
        )

        return self.async_show_form(
            step_id="reconfigure" if reconfigure_entry else "user",
            data_schema=schema,
            errors=errors,
        )
