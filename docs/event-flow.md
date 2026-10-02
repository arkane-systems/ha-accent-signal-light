# Event flow: accent, then a nested signal

Flow of events through the integration when an accent is applied and a signal
is then set on top of it. Assumes the base layer is **on** and the underlying
light is available. Code references are to `custom_components/signal_light/`.

## Sequence

```mermaid
sequenceDiagram
    autonumber
    participant A as Automation
    participant HA as HA service layer
    participant L as SignalBaseLight (light.py)
    participant C as Coordinator
    participant U as Underlying light
    participant E as Entities (light + 4 sensors)

    Note over A,E: Phase 1 — set_accent("movie", priority 10)
    A->>HA: signal_light.set_accent(accent_name, priority, attrs)
    HA->>HA: Validate schema, route entity_id / device_id
    HA->>L: async_handle_set_accent(**kwargs)
    L->>C: async_set_accent(name, priority, attrs)
    C->>C: _normalize_attrs (one colour descriptor only)
    C->>C: Replace same-name entry, append, sort by priority desc
    C->>C: _async_apply_state()
    C->>C: _async_underlying_available()? yes
    C->>C: get_effective_state(): no signals, base on and accent stack non-empty, so accent[0].attrs
    C->>U: light.turn_on(entity_id + accent attrs), blocking
    U-->>C: returns
    C->>E: _async_notify_listeners()
    E->>E: _async_handle_coordinator_update(), then async_write_ha_state()
    Note over E: Active Accent = movie, Accent Stack = [movie]

    Note over A,E: Phase 2 — set_signal("doorbell", priority 5), nested inside the accent
    A->>HA: signal_light.set_signal(signal_name, priority, attrs)
    HA->>L: async_handle_set_signal(**kwargs)
    L->>C: async_set_signal(name, priority, attrs)
    C->>C: Normalize, replace same-name entry, sort signal queue
    C->>C: _async_apply_state()
    C->>C: get_effective_state(): signal queue non-empty and base on, so signal[0].attrs
    Note over C: The accent loses on the light even though its priority is higher. Signals always outrank accents.
    C->>U: light.turn_on(entity_id + signal attrs), blocking
    C->>E: _async_notify_listeners()
    Note over E: Active Signal = doorbell, Active Accent = movie (still in stack)

    Note over A,E: Phase 3 — clear_signal("doorbell")
    A->>HA: signal_light.clear_signal(signal_name)
    HA->>L: async_handle_clear_signal
    L->>C: async_clear_signal(name)
    C->>C: Remove from queue, then _async_apply_state()
    C->>C: get_effective_state(): queue empty, so accent[0].attrs
    C->>U: light.turn_on(accent attrs), accent restored
    C->>E: _async_notify_listeners()
```

## Layer evaluation

`SignalLightCoordinator.get_effective_state()` runs top-down on every apply:

```mermaid
flowchart TD
    S{Signal queue non-empty?} -- no --> AC
    S -- yes --> W{"base on OR signal priority > wake priority?"}
    W -- yes --> SIG["ON with signal[0].attrs"]
    W -- no --> AC
    AC{"Base on AND accent stack non-empty?"}
    AC -- yes --> ACC["ON with accent[0].attrs"]
    AC -- no --> BASE["Base layer: base_on and base_attrs"]
```

## Notes

- **Two kinds of priority:** signal and accent priorities are never compared
  against each other. The signal queue is checked first, so any active signal
  beats any accent. Priority only orders entries within a layer.
- **Wake priority:** if the base layer is **off**, a signal only lights the
  light when its priority is above `signal_wake_priority`. Accents never wake
  an off light, because they require the base layer to be on.
- **Order of side effects:** the physical light is updated first, with
  `blocking=True`. Listeners are notified afterwards, so entity and sensor
  states change only once the hardware call returns.
- **Unavailable light:** if the underlying light is unavailable,
  `_async_apply_state` returns early. Layers are still updated and listeners
  still fire; the apply is retried when
  `_async_handle_underlying_state_change` sees the light become available.
- **Persistence:** accent and signal layers are in-memory only and are not
  restored across a Home Assistant restart.
