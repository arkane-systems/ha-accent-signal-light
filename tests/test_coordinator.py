"""Tests for SignalLightCoordinator -- the three-layer priority model.

Includes a regression suite for GH #6: a base-layer effect used to stick
around forever once set, because it was never cleared from the merged
base-layer attrs when a plain colour update came in afterwards.
"""

from __future__ import annotations

from custom_components.signal_light.coordinator import SignalLightCoordinator

from .conftest import UNDERLYING_ENTITY_ID

# ── _normalize_attrs ─────────────────────────────────────────────────────────


def test_normalize_attrs_collapses_multiple_color_keys() -> None:
    attrs = {"hs_color": (10.0, 20.0), "rgb_color": (1, 2, 3)}
    SignalLightCoordinator._normalize_attrs(attrs)
    assert attrs == {"rgb_color": (1, 2, 3)}


def test_normalize_attrs_noop_single_key() -> None:
    attrs = {"hs_color": (10.0, 20.0), "brightness": 100}
    SignalLightCoordinator._normalize_attrs(attrs)
    assert attrs == {"hs_color": (10.0, 20.0), "brightness": 100}


# ── get_effective_state ──────────────────────────────────────────────────────


async def test_effective_state_base_only(coordinator: SignalLightCoordinator) -> None:
    await coordinator.async_set_base_on({"brightness": 100})
    assert coordinator.get_effective_state() == (True, {"brightness": 100})


async def test_effective_state_base_off(coordinator: SignalLightCoordinator) -> None:
    assert coordinator.get_effective_state() == (False, {})


async def test_effective_state_accent_overrides_base_when_base_on(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_base_on({"brightness": 100})
    await coordinator.async_set_accent("test", 10, {"brightness": 200})
    on, attrs = coordinator.get_effective_state()
    assert on is True
    assert attrs == {"brightness": 200}


async def test_effective_state_accent_ignored_when_base_off(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_accent("test", 10, {"brightness": 200})
    assert coordinator.get_effective_state() == (False, {})


async def test_effective_state_signal_overrides_accent_when_base_on(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_base_on({"brightness": 100})
    await coordinator.async_set_accent("acc", 10, {"brightness": 200})
    await coordinator.async_set_signal("sig", 20, {"brightness": 300})
    on, attrs = coordinator.get_effective_state()
    assert on is True
    assert attrs == {"brightness": 300}


async def test_effective_state_signal_above_wake_priority_overrides_base_off(
    coordinator: SignalLightCoordinator,
) -> None:
    # signal_wake_priority defaults to 5000 in the mock_config_entry fixture.
    await coordinator.async_set_signal("sig", 6000, {"brightness": 300})
    on, attrs = coordinator.get_effective_state()
    assert on is True
    assert attrs == {"brightness": 300}


async def test_effective_state_signal_below_wake_priority_does_not_override_base_off(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_signal("sig", 100, {"brightness": 300})
    assert coordinator.get_effective_state() == (False, {})


# ── Accent stack ──────────────────────────────────────────────────────────────


async def test_accent_stack_replace_by_name(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_accent("acc", 10, {"brightness": 100})
    await coordinator.async_set_accent("acc", 10, {"brightness": 200})
    assert len(coordinator.accent_stack) == 1
    assert coordinator.active_accent["attrs"] == {"brightness": 200}


async def test_accent_stack_priority_sort_order(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_accent("low", 5, {})
    await coordinator.async_set_accent("high", 50, {})
    assert coordinator.active_accent["name"] == "high"


async def test_accent_clear_falls_through_to_next(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_accent("low", 5, {})
    await coordinator.async_set_accent("high", 50, {})
    await coordinator.async_clear_accent("high")
    assert coordinator.active_accent["name"] == "low"


# ── Signal queue ──────────────────────────────────────────────────────────────


async def test_signal_queue_replace_by_name(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_signal("sig", 10, {"brightness": 100})
    await coordinator.async_set_signal("sig", 10, {"brightness": 200})
    assert len(coordinator.signal_queue) == 1
    assert coordinator.active_signal["attrs"] == {"brightness": 200}


async def test_signal_clear_falls_through_to_next(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_signal("low", 5, {})
    await coordinator.async_set_signal("high", 50, {})
    await coordinator.async_clear_signal("high")
    assert coordinator.active_signal["name"] == "low"


# ── GH #6 regression: base-layer effect retention ────────────────────────────


async def test_effect_cleared_by_manual_color_update(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"effect": "rainbow"})
    await coordinator.async_set_base_on({"hs_color": (10.0, 20.0)})

    assert "effect" not in coordinator.base_attrs
    last_call = light_service_calls["turn_on"][-1]
    assert "effect" not in last_call.data
    assert last_call.data["hs_color"] == (10.0, 20.0)


async def test_effect_replaced_by_new_explicit_effect(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_base_on({"effect": "rainbow"})
    await coordinator.async_set_base_on({"effect": "colorloop"})
    assert coordinator.base_attrs["effect"] == "colorloop"


async def test_effect_preserved_by_brightness_only_update(
    coordinator: SignalLightCoordinator,
) -> None:
    await coordinator.async_set_base_on({"effect": "rainbow"})
    await coordinator.async_set_base_on({"brightness": 100})
    assert coordinator.base_attrs["effect"] == "rainbow"
    assert coordinator.base_attrs["brightness"] == 100


# ── Physical-light application ───────────────────────────────────────────────


async def test_async_set_base_on_merges_and_applies(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"brightness": 100})
    await coordinator.async_set_base_on({"hs_color": (1.0, 2.0)})

    last_call = light_service_calls["turn_on"][-1]
    assert last_call.data["entity_id"] == UNDERLYING_ENTITY_ID
    assert last_call.data["brightness"] == 100
    assert last_call.data["hs_color"] == (1.0, 2.0)


async def test_async_set_base_off_calls_turn_off(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"brightness": 100})
    await coordinator.async_set_base_off()

    assert len(light_service_calls["turn_off"]) == 1
    assert light_service_calls["turn_off"][0].data["entity_id"] == UNDERLYING_ENTITY_ID


async def test_apply_skipped_when_underlying_unavailable(
    hass, coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    hass.states.async_set(UNDERLYING_ENTITY_ID, "unavailable", {})
    await coordinator.async_set_base_on({"brightness": 100})

    assert light_service_calls["turn_on"] == []


async def test_reapply_on_underlying_available_transition(
    hass, coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    hass.states.async_set(UNDERLYING_ENTITY_ID, "unavailable", {})
    await coordinator.async_set_base_on({"brightness": 100})
    assert light_service_calls["turn_on"] == []

    hass.states.async_set(UNDERLYING_ENTITY_ID, "on", {})
    await hass.async_block_till_done()

    assert len(light_service_calls["turn_on"]) == 1
    assert light_service_calls["turn_on"][0].data["brightness"] == 100


async def test_accent_clear_reapply_only_when_active_changed(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({})
    await coordinator.async_set_accent("high", 50, {"brightness": 200})
    await coordinator.async_set_accent("low", 5, {"brightness": 50})
    calls_before = len(light_service_calls["turn_on"])

    # Clearing the inactive ("low") accent should not trigger a re-apply.
    await coordinator.async_clear_accent("low")
    assert len(light_service_calls["turn_on"]) == calls_before

    # Clearing the active ("high") accent should trigger a re-apply.
    await coordinator.async_clear_accent("high")
    assert len(light_service_calls["turn_on"]) == calls_before + 1


async def test_signal_clear_reapply_only_when_active_changed(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({})
    await coordinator.async_set_signal("high", 50, {"brightness": 200})
    await coordinator.async_set_signal("low", 5, {"brightness": 50})
    calls_before = len(light_service_calls["turn_on"])

    await coordinator.async_clear_signal("low")
    assert len(light_service_calls["turn_on"]) == calls_before

    await coordinator.async_clear_signal("high")
    assert len(light_service_calls["turn_on"]) == calls_before + 1


# ── One-shot transition on base-layer commands ───────────────────────────────


async def test_base_on_transition_is_forwarded_to_underlying(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"brightness": 100}, transition=5)

    assert light_service_calls["turn_on"][-1].data["transition"] == 5


async def test_base_on_without_transition_sends_none(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"brightness": 100})

    assert "transition" not in light_service_calls["turn_on"][-1].data


async def test_base_on_zero_transition_is_forwarded(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    # 0 means "snap, no fade" -- it must not be mistaken for "not given".
    await coordinator.async_set_base_on({"brightness": 100}, transition=0)

    assert light_service_calls["turn_on"][-1].data["transition"] == 0


async def test_base_on_transition_is_not_persisted_in_base_attrs(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"brightness": 100}, transition=5)

    assert "transition" not in coordinator.base_attrs

    # A later re-apply of the base layer (here: a signal above it clears)
    # must not replay the earlier command's fade.
    await coordinator.async_set_signal("sig", 10, {"brightness": 200})
    await coordinator.async_clear_signal("sig")

    assert "transition" not in light_service_calls["turn_on"][-1].data


async def test_base_off_transition_is_forwarded_to_underlying(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"brightness": 100})
    await coordinator.async_set_base_off(transition=5)

    assert light_service_calls["turn_off"][-1].data["transition"] == 5


async def test_base_off_without_transition_sends_none(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"brightness": 100})
    await coordinator.async_set_base_off()

    assert "transition" not in light_service_calls["turn_off"][-1].data


async def test_base_off_transition_forwarded_when_accent_active(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    # Accents never keep the light on once the base layer is off, so the
    # fade-out belongs to the turn_off that results.
    await coordinator.async_set_base_on({"brightness": 100})
    await coordinator.async_set_accent("mood", 10, {"brightness": 200})
    await coordinator.async_set_base_off(transition=5)

    assert light_service_calls["turn_off"][-1].data["transition"] == 5


async def test_base_on_transition_applies_when_accent_takes_over(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_accent("mood", 10, {"brightness": 200})
    await coordinator.async_set_base_on({}, transition=5)

    last_call = light_service_calls["turn_on"][-1]
    assert last_call.data["brightness"] == 200
    assert last_call.data["transition"] == 5


async def test_layer_own_transition_still_forwarded(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"brightness": 100})
    await coordinator.async_set_signal("sig", 10, {"brightness": 200, "transition": 2})

    assert light_service_calls["turn_on"][-1].data["transition"] == 2


async def test_command_transition_overrides_layer_transition(
    coordinator: SignalLightCoordinator, light_service_calls: dict[str, list]
) -> None:
    await coordinator.async_set_base_on({"brightness": 100})
    await coordinator.async_set_signal("sig", 10, {"brightness": 200, "transition": 2})
    await coordinator.async_set_base_on({"brightness": 50}, transition=5)

    assert light_service_calls["turn_on"][-1].data["transition"] == 5
