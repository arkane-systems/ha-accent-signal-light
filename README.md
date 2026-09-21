# ![icon](assets/ha-accent-signal-light-32x32.png) Accent & Signal Light — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

**Accent & Signal Light** is a Home Assistant custom integration that lets you manage
accent and notification lights without writing complex bookkeeping automations.
Instead of tracking "which automation last changed the colour of my lamp" across
dozens of rules, Accent & Signal Light gives every use-case its own named slot in a
priority-sorted stack or queue.  When a slot is cleared the light automatically
reverts to the next lower layer — zero cleanup automation required.

---

## Table of contents

1. [Concepts — the three-layer model](#1-concepts--the-three-layer-model)
2. [Installation](#2-installation)
3. [Configuration](#3-configuration)
4. [Entities created](#4-entities-created)
5. [Services reference](#5-services-reference)
6. [Example automations](#6-example-automations)
7. [Architecture notes](#7-architecture-notes)

---

## 1. Concepts — the three-layer model

Accent & Signal Light wraps a single physical light entity (or light group) in a
**three-layer priority model**.  Layers are evaluated from highest to lowest;
the topmost non-empty layer controls the physical light.

```
┌───────────────────────────────────────────────────────────┐
│  SIGNAL LAYER  (highest priority)                         │
│  Priority queue of named signals.                         │
│  Highest-priority signal controls the light.              │
│  When cleared, next signal takes over (or falls through). │
├───────────────────────────────────────────────────────────┤
│  ACCENT LAYER  (medium priority)                          │
│  Priority stack of named accents.                         │
│  Highest-priority accent controls the light when no       │
│  signal is active.                                        │
│  When cleared, next accent takes over (or falls through). │
├───────────────────────────────────────────────────────────┤
│  BASE LAYER    (lowest priority)                          │
│  Default light state — on/off, colour, brightness.        │
│  Exposed as a standard HA light entity.                   │
│  Active only when accent and signal layers are empty.     │
└───────────────────────────────────────────────────────────┘
         │
         ▼ controls
   ┌──────────────┐
   │ Physical     │
   │ light entity │
   └──────────────┘
```

### Base layer

The base layer is the "ambient" default.  Set it once (e.g. warm white at 40 %
brightness) and it will be applied whenever nothing else is active.  It is
exposed as a standard `light` entity, so you can control it from dashboards,
voice assistants, or automations exactly like any other light.

### Accent layer

Accents are long-lived overrides — for example:

| Accent name      | Priority | Use-case                                       |
| ---------------- | -------- | ---------------------------------------------- |
| `holiday_4th`    | 20       | Red/white/blue all week                        |
| `movie_mode`     | 60       | Dim warm light when the TV is on               |
| `party`          | 80       | Colour cycle while the party playlist plays    |

Multiple accents can coexist.  The one with the **highest priority** controls
the light.  When you clear `party`, `movie_mode` (if still present) takes over
immediately — no extra automation needed.  If the base light is off, accents are
still tracked but they will not turn the physical light on by themselves.

Accents are added/removed via the `signal_light.set_accent` and
`signal_light.clear_accent` services.

### Signal layer

Signals are short-lived notifications — for example:

| Signal name       | Priority | Use-case                                     |
| ----------------- | -------- | -------------------------------------------- |
| `doorbell`        | 90       | Flash white when someone rings the bell      |
| `laundry_done`    | 70       | Slow blue pulse when the washing is finished |
| `timer_expired`   | 80       | Red flash when a kitchen timer ends          |

The highest-priority active signal overrides the accent and base layers.  When
it is cleared (e.g. the user acknowledges the doorbell) the next signal (if
any) takes over, or the accent/base layer resumes.  If the base light is off,
signals remain queued and only turn the light on when their priority is greater
than the configured signal wake priority.

Signals are added/removed via the `signal_light.set_signal` and
`signal_light.clear_signal` services.

---

## 2. Installation

### HACS (recommended)

1. Open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/arkane-systems/ha-accent-signal-light` as an
   **Integration** repository.
3. Search for **Accent & Signal Light** and click **Download**.
4. Restart Home Assistant.

### Manual

1. Copy the `custom_components/signal_light/` directory into your HA
   configuration directory under `custom_components/`.
2. Restart Home Assistant.

---

## 3. Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Accent & Signal Light** and click it.
3. Fill in the form:
   - **Name** — a label for this instance (e.g. "Living Room Accent & Signal Light").
   - **Underlying light entity** — the `light.*` entity that Accent & Signal Light will
     control (may be a light group).
   - **Signal wake priority** — signals must have a priority greater than this
     value to turn the light on while the base layer is off.
4. Click **Submit**.

You can create multiple Accent & Signal Light instances — one per physical light or
group you want to manage this way.

---

## 4. Entities created

Each Accent & Signal Light instance creates **five entities**, all grouped under a
single HA device named after the instance.

| Entity type | Name suffix       | Description                                      |
| ----------- | ----------------- | ------------------------------------------------ |
| `light`     | Base              | Base-layer light — controls the default state.   |
| `sensor`    | Active Signal     | Name of the top-priority signal, or "none".      |
| `sensor`    | Active Accent     | Name of the top-priority accent, or "none".      |
| `sensor`    | Signal Queue      | Count of queued signals; full queue in attrs.    |
| `sensor`    | Accent Stack      | Count of stacked accents; full stack in attrs.   |

### Base light entity

The base light (`light.<name>_base`) behaves like any standard HA light.  Use
it in dashboards or automations to set the default ambient state.  Note that
its reported state always reflects the **base layer**, even when an accent or
signal is overriding the physical light.  It also exposes
`underlying_entity_id` as an attribute — the `entity_id` of the physical light
(or light group) this instance wraps — useful for debugging or for automations
that need to inspect the underlying entity's real state directly.

### Sensor entities

All sensor entities report their data as both `state` and `extra_state_attributes`.

**Active Signal sensor:**
```
state:       "doorbell"
attributes:
  signal_wake_priority: 5000
  priority:  90
  hs_color:  [0, 0]
  brightness: 255
```
`signal_wake_priority` is the configured wake threshold and is always present,
even when no signal is active (in which case `priority` and the light
attributes are absent).

**Active Accent sensor:**
```
state:       "doorbell"
attributes:
  priority:  90
  hs_color:  [0, 0]
  brightness: 255
```

**Signal Queue / Accent Stack sensors:**
```
state:    2
attributes:
  queue:
    - name:     "doorbell"
      priority: 90
      attrs:    {hs_color: [0, 0], brightness: 255}
    - name:     "laundry_done"
      priority: 70
      attrs:    {hs_color: [240, 80], brightness: 128}
```

---

## 5. Services reference

All services are in the `signal_light` domain and target the **base-layer
light entity** (or its device) of the instance you want to control.

---

### `signal_light.set_signal`

Add or update a signal in the priority queue.  If a signal with the given name
already exists it is replaced (handy for updating colour without a separate
clear).

| Field            | Type    | Required | Default | Description                                |
| ---------------- | ------- | -------- | ------- | ------------------------------------------ |
| `entity_id`      | entity  | ✓        | —       | The base-layer light of the target instance |
| `signal_name`    | string  | ✓        | —       | Unique name for this signal                |
| `priority`       | integer | —        | 50      | Higher values take precedence              |
| `brightness`     | integer | —        | —       | 0–255                                      |
| `brightness_pct` | float   | —        | —       | 0–100 %                                    |
| `color_name`     | string  | —        | —       | Named colour (e.g. `"red"`)                |
| `hs_color`       | list    | —        | —       | `[hue, saturation]`                        |
| `rgb_color`      | list    | —        | —       | `[r, g, b]`                                |
| `color_temp_kelvin` | int  | —        | —       | Kelvin (1000–10000)                        |
| `effect`         | string  | —        | —       | Effect name                                |
| `transition`     | float   | —        | —       | Seconds                                    |

---

### `signal_light.clear_signal`

Remove a signal from the priority queue.

| Field         | Type   | Required | Description                    |
| ------------- | ------ | -------- | ------------------------------ |
| `entity_id`   | entity | ✓        | The base-layer light           |
| `signal_name` | string | ✓        | Name of the signal to remove   |

---

### `signal_light.set_accent`

Add or update an accent in the priority stack.

| Field            | Type    | Required | Default | Description                                |
| ---------------- | ------- | -------- | ------- | ------------------------------------------ |
| `entity_id`      | entity  | ✓        | —       | The base-layer light of the target instance |
| `accent_name`    | string  | ✓        | —       | Unique name for this accent                |
| `priority`       | integer | —        | 50      | Higher values take precedence              |
| `brightness`     | integer | —        | —       | 0–255                                      |
| `brightness_pct` | float   | —        | —       | 0–100 %                                    |
| `color_name`     | string  | —        | —       | Named colour (e.g. `"red"`)                |
| `hs_color`       | list    | —        | —       | `[hue, saturation]`                        |
| `rgb_color`      | list    | —        | —       | `[r, g, b]`                                |
| `color_temp_kelvin` | int  | —        | —       | Kelvin (1000–10000)                        |
| `effect`         | string  | —        | —       | Effect name                                |
| `transition`     | float   | —        | —       | Seconds                                    |

---

### `signal_light.clear_accent`

Remove an accent from the priority stack.

| Field         | Type   | Required | Description                    |
| ------------- | ------ | -------- | ------------------------------ |
| `entity_id`   | entity | ✓        | The base-layer light           |
| `accent_name` | string | ✓        | Name of the accent to remove   |

---

## 6. Example automations

### Door-bell flash

```yaml
# Turn the living-room signal light white when the doorbell is pressed.
# The flash lasts until the automation clears it (e.g. after 30 s).
automation:
  - alias: "Doorbell: signal on"
    trigger:
      - platform: state
        entity_id: binary_sensor.doorbell
        to: "on"
    action:
      - service: signal_light.set_signal
        target:
          entity_id: light.living_room_signal_light_base
        data:
          signal_name: doorbell
          priority: 90
          hs_color: [0, 0]   # white
          brightness: 255

  - alias: "Doorbell: signal off after 30 s"
    trigger:
      - platform: state
        entity_id: binary_sensor.doorbell
        to: "on"
        for: "00:00:30"
    action:
      - service: signal_light.clear_signal
        target:
          entity_id: light.living_room_signal_light_base
        data:
          signal_name: doorbell
```

### 4th of July accent

```yaml
# Apply a patriotic accent at midnight starting July 1st (i.e. the very
# beginning of July 1st) and remove it at midnight starting July 5th
# (i.e. the very beginning of July 5th, so the accent is active through
# the end of July 4th).  Any higher-priority accents or signals still take
# precedence.
automation:
  - alias: "4th of July accent: activate"
    trigger:
      - platform: time
        at: "00:00:00"
    condition:
      - condition: template
        value_template: "{{ now().month == 7 and now().day == 1 }}"
    action:
      - service: signal_light.set_accent
        target:
          entity_id: light.living_room_signal_light_base
        data:
          accent_name: fourth_of_july
          priority: 20
          rgb_color: [255, 0, 0]   # start red; rotate via separate automation

  - alias: "4th of July accent: deactivate"
    trigger:
      - platform: time
        at: "00:00:00"
    condition:
      - condition: template
        value_template: "{{ now().month == 7 and now().day == 5 }}"
    action:
      - service: signal_light.clear_accent
        target:
          entity_id: light.living_room_signal_light_base
        data:
          accent_name: fourth_of_july
```

### Movie mode accent

```yaml
# Activate movie-mode (dim, warm) while the TV is on.  Because this has a
# higher priority than the holiday accent, it overrides it during movie time.
automation:
  - alias: "Movie mode: activate"
    trigger:
      - platform: state
        entity_id: media_player.living_room_tv
        to: "playing"
    action:
      - service: signal_light.set_accent
        target:
          entity_id: light.living_room_signal_light_base
        data:
          accent_name: movie_mode
          priority: 60
          brightness: 40
          color_temp_kelvin: 2700

  - alias: "Movie mode: deactivate"
    trigger:
      - platform: state
        entity_id: media_player.living_room_tv
        to: "idle"
      - platform: state
        entity_id: media_player.living_room_tv
        to: "off"
    action:
      - service: signal_light.clear_accent
        target:
          entity_id: light.living_room_signal_light_base
        data:
          accent_name: movie_mode
```

### Dashboard visibility

Add the four sensor entities to a dashboard card to see what is currently
active at a glance:

```yaml
type: entities
title: Living Room Accent & Signal Light
entities:
  - entity: light.living_room_signal_light_base
  - entity: sensor.living_room_signal_light_active_signal
  - entity: sensor.living_room_signal_light_active_accent
  - entity: sensor.living_room_signal_light_signal_queue
  - entity: sensor.living_room_signal_light_accent_stack
```

---

## 7. Architecture notes

### Coordinator

The integration uses a custom `SignalLightCoordinator` (not HA's built-in
`DataUpdateCoordinator`, which is designed for polling).  The coordinator
owns all mutable state and is the single place where state transitions happen.

Entities register a `_remove_listener` callback in `async_added_to_hass` and
de-register in `async_will_remove_from_hass`.  The coordinator calls all
listeners after every mutation, triggering a `write_ha_state` on each entity.

### Priority ordering

Both the accent stack and signal queue are stored as Python lists sorted by
`priority` descending.  Insertion and deletion are O(n) — entirely adequate
for the expected number of entries (typically < 10).  The `name` field acts as
a primary key: setting an existing name replaces the entry.

### State restoration

The base-layer light entity extends `RestoreEntity`.  On startup it reads its
previous state from the HA recorder and re-applies it to the coordinator,
ensuring the base layer survives restarts.

Accent and signal layers are intentionally **not** persisted.  They are
expected to be re-populated by automations (e.g. via `homeassistant.start`
triggers).  This avoids stale signals lingering after a restart.

### Physical-light control

Every time any layer changes, `_async_apply_state()` is called.  It evaluates
the effective state top-down and calls `light.turn_on` or `light.turn_off` on
the underlying entity.  The call is made with `blocking=True` to ensure the
light state is consistent before listeners are notified.  When the base layer is
off, accents never wake the light and only signals above the configured wake
priority may do so; queued accents/signals still take effect immediately once
the base layer is turned back on.

On startup, if the underlying light is not yet available, apply calls are
deferred.  The coordinator listens for the underlying entity becoming available
and re-applies the effective state once it comes online.

Before any `turn_on` call, color descriptors are normalized so only one of
`color_name`, `hs_color`, `rgb_color`, `rgbw_color`, `rgbww_color`, `xy_color`,
or `color_temp_kelvin` is sent in the payload.

### Capability mirroring

The base-layer light mirrors capability metadata from the underlying light so
UI controls stay aligned with the real hardware/group:

* `supported_color_modes`
* `supported_features`
* `effect_list`
* `min_color_temp_kelvin` / `max_color_temp_kelvin`

### Entity services

The four `signal_light.*` services are registered as *entity services* on the
`light` platform.  This means:

* HA validates the `entity_id` / `device_id` target automatically.
* The service is routed to `async_handle_set_signal` / etc. on the matching
  `SignalBaseLight` entity.
* Multiple instances are fully isolated — calling the service with one
  instance's `entity_id` never affects another.
