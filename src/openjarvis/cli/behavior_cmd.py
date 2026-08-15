"""CLI commands for teaching and evaluating natural-language behavior."""

from __future__ import annotations

import json
from pathlib import Path

import click

from openjarvis.behavior import (
    BehaviorResolver,
    BehaviorStore,
    SUPPORTED_ACTIONS,
    entity_from_mapping,
    evaluate_cases,
    load_cases,
)
from openjarvis.behavior.dataset import write_seed_dataset


@click.group("behavior")
def behavior_group() -> None:
    """Teach Ophanim how you naturally phrase device requests."""


@behavior_group.command("teach")
@click.argument("text")
@click.option(
    "--action",
    required=True,
    type=click.Choice(sorted(SUPPORTED_ACTIONS)),
)
@click.option(
    "--entity",
    default=None,
    help="Canonical Home Assistant entity id, for example light.livingroomlamp.",
)
@click.option("--recent-topic", default=None, help="Canonical entity id of the recent topic.")
@click.option("--predicted-action", default=None)
@click.option("--predicted-entity", default=None)
@click.option(
    "--parameters",
    default="{}",
    help='Corrected action parameters as JSON, for example {"brightness_pct":40}.',
)
@click.option(
    "--predicted-parameters",
    default="{}",
    help="Model parameters as JSON when recording the before/after pair.",
)
def behavior_teach(
    text: str,
    action: str,
    entity: str | None,
    recent_topic: str | None,
    predicted_action: str | None,
    predicted_entity: str | None,
    parameters: str,
    predicted_parameters: str,
) -> None:
    """Save a before/after correction for TEXT."""
    try:
        corrected_parameters = json.loads(parameters)
        model_parameters = json.loads(predicted_parameters)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Parameters must be valid JSON: {exc}") from exc
    if not isinstance(corrected_parameters, dict) or not isinstance(model_parameters, dict):
        raise click.ClickException("Parameters must be JSON objects.")
    with BehaviorStore() as store:
        resolver = BehaviorResolver(store)
        example = resolver.teach(
            before_text=text,
            corrected_action=action,
            corrected_entity=entity,
            recent_topic=recent_topic,
            predicted={
                "action": predicted_action,
                "entity_id": predicted_entity,
                "parameters": model_parameters,
            },
            corrected_parameters=corrected_parameters,
        )
    click.echo(
        f"Saved behavior example {example.id}: "
        f"{example.before_text!r} -> {example.corrected_action}"
    )


@behavior_group.command("generate")
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("examples/behavior/ophanim_intent_seed.jsonl"),
    show_default=True,
)
@click.option("--examples-per-intent", type=click.IntRange(min=1, max=100), default=8)
def behavior_generate(output_path: Path, examples_per_intent: int) -> None:
    """Generate catalog-wide natural-language seed examples as JSONL."""
    count = write_seed_dataset(output_path, examples_per_intent=examples_per_intent)
    click.echo(f"Generated {count} intent examples at {output_path}")


@behavior_group.command("stats")
def behavior_stats() -> None:
    """Show how many corrections Ophanim has learned."""
    with BehaviorStore() as store:
        click.echo(f"Behavior corrections: {store.count()}")


@behavior_group.command("eval")
@click.option(
    "--path",
    "dataset_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--entities",
    "entities_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
def behavior_eval(dataset_path: Path, entities_path: Path) -> None:
    """Run a natural-language behavior regression dataset."""
    values = json.loads(entities_path.read_text(encoding="utf-8"))
    entities = tuple(entity_from_mapping(value) for value in values)
    cases = load_cases(dataset_path)
    with BehaviorStore(":memory:") as store:
        report = evaluate_cases(cases, entities=entities, resolver=BehaviorResolver(store))
    click.echo(f"Behavior eval: {report.passed}/{report.total} passed ({report.accuracy:.1%})")
    for result in report.results:
        if not result.passed:
            click.echo(
                f"FAIL {result.case.case_id}: expected "
                f"{result.case.expected_status}/{result.case.expected_action}/"
                f"{result.case.expected_entity_id}; got "
                f"{result.actual_status}/{result.actual_action}/{result.actual_entity_id}"
            )
    if report.passed != report.total:
        raise click.ClickException("Behavior regression evaluation failed")


__all__ = ["behavior_group"]
