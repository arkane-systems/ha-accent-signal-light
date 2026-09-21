"""Tests for the Accent & Signal Light config flow."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_light.const import (
    CONF_SIGNAL_WAKE_PRIORITY,
    CONF_UNDERLYING_ENTITY_ID,
    DATA_COORDINATOR,
    DEFAULT_SIGNAL_WAKE_PRIORITY,
    DOMAIN,
)
from custom_components.signal_light.config_flow import (
    _existing_underlying_ids,
    _get_light_entity_ids,
)

from .conftest import UNDERLYING_ENTITY_ID

SECOND_UNDERLYING_ENTITY_ID = "light.test_light_2"


def _register_second_light(hass: HomeAssistant) -> str:
    """Register a second fake light entity, distinct from UNDERLYING_ENTITY_ID."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "light", "test", "unique_test_light_2", suggested_object_id="test_light_2"
    )
    hass.states.async_set(SECOND_UNDERLYING_ENTITY_ID, "on", {})
    return SECOND_UNDERLYING_ENTITY_ID


async def test_user_step_shows_form_with_light_choices(hass, mock_underlying_light) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_step_creates_entry(hass, mock_underlying_light) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "My Signal Light",
            CONF_UNDERLYING_ENTITY_ID: mock_underlying_light,
            CONF_SIGNAL_WAKE_PRIORITY: DEFAULT_SIGNAL_WAKE_PRIORITY,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Signal Light"
    assert result["data"][CONF_UNDERLYING_ENTITY_ID] == mock_underlying_light
    assert result["data"][CONF_SIGNAL_WAKE_PRIORITY] == DEFAULT_SIGNAL_WAKE_PRIORITY


async def test_user_step_rejects_entity_not_a_light(hass) -> None:
    # No light entities registered, so the schema falls back to cv.string
    # and the manual "must be a known light" check in async_step_user runs.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "My Signal Light",
            CONF_UNDERLYING_ENTITY_ID: "light.not_a_real_light",
            CONF_SIGNAL_WAKE_PRIORITY: DEFAULT_SIGNAL_WAKE_PRIORITY,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_UNDERLYING_ENTITY_ID: "entity_not_found"}


async def test_user_step_rejects_already_configured_entity(
    hass, mock_config_entry
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Second Instance",
            CONF_UNDERLYING_ENTITY_ID: UNDERLYING_ENTITY_ID,
            CONF_SIGNAL_WAKE_PRIORITY: DEFAULT_SIGNAL_WAKE_PRIORITY,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_UNDERLYING_ENTITY_ID: "already_configured"}


# ── Reconfigure flow ─────────────────────────────────────────────────────────


async def test_reconfigure_step_shows_form(hass, mock_config_entry) -> None:
    result = await mock_config_entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


async def test_reconfigure_step_updates_wake_priority_in_place(
    hass, mock_config_entry
) -> None:
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Renamed Instance",
            CONF_UNDERLYING_ENTITY_ID: UNDERLYING_ENTITY_ID,
            CONF_SIGNAL_WAKE_PRIORITY: 1234,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.title == "Renamed Instance"
    assert mock_config_entry.data[CONF_UNDERLYING_ENTITY_ID] == UNDERLYING_ENTITY_ID
    assert mock_config_entry.data[CONF_SIGNAL_WAKE_PRIORITY] == 1234


async def test_reconfigure_step_allows_keeping_same_underlying_entity(
    hass, mock_config_entry
) -> None:
    """Resubmitting the entry's own current entity must not look like a duplicate."""
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": mock_config_entry.title,
            CONF_UNDERLYING_ENTITY_ID: UNDERLYING_ENTITY_ID,
            CONF_SIGNAL_WAKE_PRIORITY: DEFAULT_SIGNAL_WAKE_PRIORITY,
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_step_allows_changing_underlying_entity(
    hass, mock_config_entry
) -> None:
    second_entity_id = _register_second_light(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": mock_config_entry.title,
            CONF_UNDERLYING_ENTITY_ID: second_entity_id,
            CONF_SIGNAL_WAKE_PRIORITY: DEFAULT_SIGNAL_WAKE_PRIORITY,
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_UNDERLYING_ENTITY_ID] == second_entity_id
    assert mock_config_entry.unique_id == second_entity_id


async def test_reconfigure_step_rejects_entity_claimed_by_another_entry(
    hass, mock_config_entry
) -> None:
    second_entity_id = _register_second_light(hass)

    other_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Other Instance",
        unique_id=second_entity_id,
        data={
            CONF_UNDERLYING_ENTITY_ID: second_entity_id,
            CONF_SIGNAL_WAKE_PRIORITY: DEFAULT_SIGNAL_WAKE_PRIORITY,
        },
    )
    other_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": mock_config_entry.title,
            CONF_UNDERLYING_ENTITY_ID: second_entity_id,
            CONF_SIGNAL_WAKE_PRIORITY: DEFAULT_SIGNAL_WAKE_PRIORITY,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_UNDERLYING_ENTITY_ID: "already_configured"}


async def test_reconfigure_step_reloads_and_preserves_entity_ids(
    hass, setup_integration
) -> None:
    """Reconfiguring must reload the entry in place, not recreate its entities."""
    entry = setup_integration
    registry = er.async_get(hass)
    base_entity_id_before = registry.async_get_entity_id(
        "light", DOMAIN, f"{entry.entry_id}_base"
    )

    second_entity_id = _register_second_light(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": entry.title,
            CONF_UNDERLYING_ENTITY_ID: second_entity_id,
            CONF_SIGNAL_WAKE_PRIORITY: DEFAULT_SIGNAL_WAKE_PRIORITY,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    base_entity_id_after = registry.async_get_entity_id(
        "light", DOMAIN, f"{entry.entry_id}_base"
    )
    assert base_entity_id_after == base_entity_id_before

    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    assert coordinator.underlying_entity_id == second_entity_id


# ── Module-level helper functions ────────────────────────────────────────────


def test_get_light_entity_ids_filters_non_light_domain(hass, mock_underlying_light) -> None:
    assert _get_light_entity_ids(hass) == [UNDERLYING_ENTITY_ID]


def test_existing_underlying_ids_reflects_configured_entries(
    hass, mock_config_entry
) -> None:
    assert _existing_underlying_ids(hass) == {UNDERLYING_ENTITY_ID}
