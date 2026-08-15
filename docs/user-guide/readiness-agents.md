# Readiness Agents

Readiness agents are scheduled managed agents that turn a few connected data
sources into a short, actionable status update. The first two built-in
workflows are:

- **Spiritual Readiness** — runs at 05:00 local time and prepares a short
  devotional with scripture, meditation, prayer, and one practical action.
- **Commute Readiness** — checks live structured traffic every five minutes
  from 05:00 through 06:00 and alerts only when departure risk changes.

## Activate the agents

Open **Agents → New Agent** in the app and choose the desired template. Replace
the bracketed values in the standing instruction before launching it.

The Spiritual Readiness template uses the `devotional_content` tool and the
`channel_send` tool. The Commute Readiness template uses `commute_readiness`,
`weather_lookup`, and `channel_send`.

## Devotional database

The local store is created on first use at:

```text
~/.openjarvis/devotional.db
```

It contains three tables:

- `devotionals` — scripture reference/text plus theme, reflection, prayer,
  practice, tradition, translation, weekday, and tags.
- `devotional_preferences` — preferred tradition, translation, worship focus,
  prayer topics, tone, and preferred time.
- `devotional_schema_meta` — schema version for future migrations.

The initial database is seeded with seven morning entries. User-supplied
entries can be added through `devotional_content(action="add", ...)`, and
preferences can be updated with
`devotional_content(action="set_preferences", ...)`.

The agent is instructed to treat stored scripture text as authoritative. It
does not invent references or claim that a generated reflection is a direct
message from God.

## Commute provider behavior

`commute_readiness` only produces a departure recommendation when the traffic
provider returns structured normal and traffic durations. If Google traffic is
unavailable and the traffic tool falls back to a search result, the agent will
report that traffic could not be verified rather than giving a false green
status.
