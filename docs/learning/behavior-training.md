# Natural-language behavior training

Ophanim's device behavior is split into three layers:

1. A model may extract a possible action and entity mention from speech.
2. The behavior resolver applies context and confidence rules, returns a canonical Home Assistant tool call, or asks one short clarification question.
3. The Home Assistant tool allow-lists the domain/service mapping and verifies the result.

The model is not allowed to invent Home Assistant entity IDs. The tool still performs the actual action and verifies the resulting state.

## Supported intent families

The shared catalog in `src/openjarvis/behavior/catalog.py` currently covers:

- power and state: on, off, toggle, state
- lights: brightness, color, and color temperature
- climate and fans: target temperature, HVAC mode, and fan percentage
- media: play, pause, stop, next, previous, and volume
- covers and locks: open, close, stop, position, lock, and unlock
- routines: scenes, scripts, and automation enable/disable/trigger
- sensors: temperature, humidity, battery, and motion

The same catalog drives the extraction prompt, resolver allow-list, tool schema,
and training generator.

## Entity and conversation context

Build `BehaviorEntity` values from the live `ContextSnapshot`:

```python
from openjarvis.behavior import BehaviorResolver, entities_from_snapshot

entities = entities_from_snapshot(snapshot)
resolution = BehaviorResolver().resolve(
    "turn it on",
    entities=entities,
    recent_topic="light.livingroomlamp",
)

if resolution.should_execute:
    result = home_assistant_tool.execute(**resolution.tool_call())
else:
    speak(resolution.clarification)
```

The entity map can contain all of the names a person might use. For example, `Living Room Lamp`, `lamp`, `reading light`, and `light.livingroomlamp` can all resolve to the same canonical ID. Generic names remain ambiguous when more than one device matches.

## Teach flow

When Ophanim makes a mistake, save the user's correction as a before/after example:

```powershell
ophanim behavior teach "turn it on" `
  --recent-topic light.livingroomlamp `
  --action turn_on `
  --entity light.livingroomlamp
```

The example is stored in the runtime `behavior.db` next to the other Ophanim data. It includes the original wording, normalized wording, predicted action/entity when available, corrected action/entity, recent topic, and optional context. Similar future requests can use the learned example without retraining a model.

The equivalent Python API is `BehaviorResolver.teach(...)`. Use canonical entity IDs for stored corrections so topic matching remains stable even when friendly names change.

For parameters, add JSON to the teach command:

```powershell
ophanim behavior teach "make it brighter" `
  --action set_brightness `
  --entity light.livingroomlamp `
  --parameters '{"brightness_pct":40}'
```

## Confidence rules

- An exact or unique entity alias is preferred.
- `it`, `that light`, `the lamp`, and similar references use the recent topic only when it maps to one live entity.
- If two devices are plausible, the resolver returns `clarify` with a short question.
- If no supported action is understood, it returns `unsupported`.
- A successful HTTP service response is not treated as success; pass the canonical call to `HomeAssistantTool`, which verifies the resulting state.

## Evaluation

Run the current 60-case set with:

```powershell
ophanim behavior eval `
  --path examples/behavior/ophanim_eval.jsonl `
  --entities examples/behavior/ophanim_entities.json
```

The set includes natural paraphrases, follow-ups, pronouns, filler words, shorthand, typos, speech-transcription mistakes, unsupported requests, and ambiguity cases. Add a case whenever a correction exposes a new phrasing pattern. Keep the evaluation set separate from the correction store so it remains a stable regression gate.

Generate a broad catalog-wide seed set whenever the catalog changes:

```powershell
ophanim behavior generate `
  --output examples/behavior/ophanim_intent_seed.jsonl `
  --examples-per-intent 8
```

This creates 248 labeled examples across the current 31 actions. They are
starter data for prompting or later supervised training, not a substitute for
a held-out evaluation set or live entity validation.

When a channel has a live Home Assistant connection, Ophanim first builds the
entity map from the current state snapshot and routes recognized speech through
the behavior resolver. The verified tool then calls only catalog-approved
Home Assistant services and checks the resulting state or attributes. The
official REST API documents the service-call and state endpoints [here](https://developers.home-assistant.io/docs/api/rest/).

## Fine-tuning later

Do not LoRA-tune Qwen until the correction store contains a useful number of high-quality examples. First make the resolver, correction loop, and evaluation set reliable. Later, export corrections as intent/entity JSONL for supervised fine-tuning, but keep entity validation, action allow-listing, and Home Assistant verification deterministic at runtime.

The MCP launcher requires `HA_TOKEN` from the environment. If a token was
previously embedded in a local launcher, revoke/rotate it in Home Assistant and
configure the replacement as an environment variable.
