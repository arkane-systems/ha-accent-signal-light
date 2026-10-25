# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Accent & Signal Light is a Home Assistant custom integration (`custom_components/signal_light/`) that
manages accent and notification lights using a three-layer priority model, avoiding the need for
bookkeeping automations that track "which automation last changed this light." Domain: `signal_light`
(unchanged from the integration's original name — see below). Platforms: `light`, `sensor`. No external
dependencies beyond Home Assistant core.

The integration was originally published as "Signal Light" but renamed to "Accent & Signal Light" to
avoid confusion with an unrelated, similarly-named existing integration. Only the display name and the
GitHub repo (`ha-accent-signal-light`) changed — the domain, entity IDs, service names, and all
`hass.data`/config-entry internals deliberately still use `signal_light`, since changing the domain
would break every existing installation's config entries, entity registry, and automations (see git
history for the rejected alternative). Don't "finish the rename" by touching the domain.

## Development workflow

There is no build or lint step — it's a pure HA integration — but there is a pytest suite, and CI runs it.

### Tests

The suite in `tests/` uses `pytest-homeassistant-custom-component`, which provides an in-memory HA core,
so no real server, network, or lights are involved. Pinned versions are in `requirements_test.txt`; the
pinned Home Assistant needs Python 3.13, which is also what CI uses. `pytest.ini` sets
`asyncio_mode = auto` and `testpaths = tests`.

```bash
pip install -r requirements_test.txt
pytest
```

- `tests/conftest.py` holds the shared fixtures: a fake underlying light, a mock config entry, a
  `coordinator` wired to fake `light.turn_on`/`light.turn_off` services (so tests can assert on exactly
  what was sent to the physical light), `setup_integration` for the full light + sensor setup, and
  `FakeCoordinator` for pure property tests.
- Layer logic goes in `test_coordinator.py`, entity behaviour in `test_light.py`/`test_sensor.py`,
  setup/unload and the service schemas in `test_init.py`, and the config flow in `test_config_flow.py`.
- When fixing a bug, add a regression test (see the GH #6 suite in `test_coordinator.py`).

### CI

Three GitHub Actions workflows in `.github/workflows/` run on every push and pull request:
`test.yml` (the pytest suite), `hassfest.yml` (Home Assistant's own integration validation), and
`validate.yml` (HACS validation).

### Manual verification

Tests don't replace trying a change on a real instance:

1. Copy `custom_components/signal_light/` into a Home Assistant instance's `custom_components/`.
2. Restart HA and add the integration via **Settings → Devices & Services → Add Integration**.
3. Exercise it through the HA UI, automations, or the four `signal_light.*` services, and check HA's log
   for errors.

## Architecture

### The three-layer model

The integration wraps one physical light entity (or light group) in a priority model evaluated
top-down; the highest non-empty layer controls the physical light:

1. **Signal layer** — short-lived notifications (doorbell, timer). A priority queue; highest-priority
   active signal wins.
2. **Accent layer** — long-lived overrides (movie mode, holiday theme). A priority stack; highest-
   priority active accent wins when no signal is active.
3. **Base layer** — the default ambient state, exposed as a standard HA `light` entity. Active only
   when accent and signal layers are empty.

Signals and accents only take effect while the base layer is **on**. When the base layer is off, accents
never turn the light on, and a signal does so only if its priority is strictly greater than the
instance's `signal_wake_priority` (default 5000); otherwise the light stays off. The base layer
represents what the user last asked for, not what the hardware is doing, so anything that switches the
underlying light directly (bypassing the base entity) leaves the base layer out of step with it.

Accent/signal entries are plain dicts: `{"name": str, "priority": int, "attrs": dict}`. Both
collections are kept sorted by `priority` descending (index 0 is always the active entry);
insertion/deletion are O(n), which is fine given the expected <10 entries per instance. `name` acts as
a primary key — setting an existing name replaces the entry in place.

### Core components

- **`coordinator.py`** — `SignalLightCoordinator`, a custom coordinator (not HA's polling-oriented
  `DataUpdateCoordinator`) that owns all mutable state and is the single place state transitions
  happen. Every mutation calls `_async_apply_state()`, which evaluates the effective state top-down and
  calls `light.turn_on()`/`light.turn_off()` on the underlying entity with `blocking=True`. If the
  underlying entity isn't available yet at startup, apply calls are deferred until the coordinator
  observes it becoming available. Before `turn_on`, color attributes are normalized so only one of
  `color_name`, `hs_color`, `rgb_color`, `rgbw_color`, `rgbww_color`, `xy_color`, or
  `color_temp_kelvin` is ever sent. A `transition` on a base-layer `turn_on`/`turn_off` is applied to
  that one underlying call only and is never stored in the base attributes (a signal's or accent's own
  `transition` is stored with it, as part of its attributes).
- **`light.py`** — `SignalBaseLight`, the base-layer light entity exposed to users/automations. Extends
  `RestoreEntity` so base-layer state survives restarts (accent/signal layers are intentionally *not*
  persisted — they're expected to be repopulated by automations, e.g. on `homeassistant.start`).
  Mirrors capability metadata (`supported_color_modes`, `supported_features`, `effect_list`,
  `min_color_temp_kelvin`/`max_color_temp_kelvin`) from the underlying light so UI controls stay
  aligned with the real hardware/group. Also hosts the entity-service handlers.
- **`sensor.py`** — four sensor entities: Active Signal, Active Accent, Signal Queue, Accent Stack.
  Each reports via both `state` and `extra_state_attributes`.
- **`__init__.py`** — `async_setup_entry`/`async_unload_entry`. Creates the coordinator, stores it in
  `hass.data[DOMAIN][entry.entry_id]`, forwards setup to platforms, and registers the four domain
  services as *entity services* on the `light` platform (not domain-level), so HA validates/routes
  `entity_id`/`device_id` targets automatically and multiple instances stay fully isolated.
- **`config_flow.py`** — UI setup and reconfigure flow. The config entry stores
  `CONF_UNDERLYING_ENTITY_ID` (the physical light's `entity_id`) and `CONF_SIGNAL_WAKE_PRIORITY`.
  Entries created before the wake priority existed lack the latter, so always read it with
  `.get(..., DEFAULT_SIGNAL_WAKE_PRIORITY)`.
- **`const.py`** — every string literal that appears in more than one place, or that carries semantic
  meaning beyond a plain dict key (domain, config keys, `hass.data` keys, service/field names, sensor
  suffixes, defaults). Use these constants rather than hardcoding duplicated strings.
- **Non-Python files** — `services.yaml` (service schemas for HA's UI), `strings.json` and
  `translations/en.json` (UI text; currently identical, so edit both together), `manifest.json`.

### Listener pattern

Entities register a callback with the coordinator in `async_added_to_hass` and deregister in
`async_will_remove_from_hass`. The coordinator invokes all registered listeners (no arguments) after
every mutation, and each listener calls `write_ha_state()` on its entity.

### Entity IDs

Entity names are `"<instance title> Base"` (the light) and `"<instance title> <Sensor Name>"`, so for an
instance named "Living Room": `light.living_room_base`, `sensor.living_room_active_signal`,
`..._active_accent`, `..._signal_queue`, `..._accent_stack` — all grouped under one HA device. The
entity_id is derived from the title when the entity is first registered; unique IDs are based on the
config entry ID, so they survive renames.

### Debugging

The four sensors show the active signal/accent and the full queue/stack in their attributes, the Active
Signal sensor also exposes `signal_wake_priority`, and the base light exposes `underlying_entity_id`.
Compare the base light's state with the underlying light's real state first: a mismatch usually means
something controlled the underlying light directly.

## Conventions

- Validation uses `voluptuous`, with schemas defined at module level (not inline). The light-attribute
  schema is duplicated deliberately between `__init__.py` and `light.py` (`_LIGHT_ATTR_SCHEMA`),
  including `color_name`.
- All integration code is async (`async def`/`await`); use the `@callback` decorator from
  `homeassistant.core` for synchronous callbacks.
- Modules have a module-level docstring explaining purpose and key concepts; classes and public
  methods have docstrings with Args/Returns; type hints throughout.
- `_LOGGER = logging.getLogger(__name__)` per module; DEBUG for routine operations, INFO/ERROR for
  notable events.

## Adding a new service

1. Add the service name constant to `const.py`.
2. Define its voluptuous schema in `__init__.py`.
3. Register it in `async_setup_entry()` in `__init__.py`.
4. Implement the handler on `SignalBaseLight` in `light.py`, delegating to a coordinator method for the
   actual state mutation.
5. Document the schema in `services.yaml`.

## Adding a new sensor

1. Add a `SENSOR_*` constant for the unique-ID suffix to `const.py`.
2. Subclass `_SignalLightSensorBase` in `sensor.py` (it already registers the coordinator listener and
   calls `write_ha_state()` on every update), passing the suffix and a display-name suffix to
   `super().__init__()`, and implement `native_value` / `extra_state_attributes`.
3. Instantiate it in `async_setup_entry()` in `sensor.py`.
