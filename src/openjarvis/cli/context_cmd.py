"""CLI entry point for live-context sources."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import click

from openjarvis.context.home_assistant import HomeAssistantContextSource
from openjarvis.context.store import ContextStore


@click.group("context")
def context() -> None:
    """Manage live state sources used by the context builder."""


@context.command("home-assistant")
@click.option(
    "--url",
    envvar="HA_URL",
    help="Home Assistant base URL; defaults to HA_URL.",
)
@click.option(
    "--db-path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Override the default ~/.openjarvis/context.db path.",
)
def home_assistant(url: str | None, db_path: Path | None) -> None:
    """Snapshot and continuously ingest Home Assistant state changes."""
    token = os.environ.get("HA_TOKEN", "")
    if not url or not token:
        raise click.ClickException(
            "Set HA_URL and HA_TOKEN before starting this source."
        )

    store = ContextStore(db_path)
    source = HomeAssistantContextSource(
        store,
        url=url,
        token=token,
    )

    click.echo(
        f"Listening for Home Assistant state changes at {source.websocket_url}"
    )
    try:
        asyncio.run(source.listen())
    except KeyboardInterrupt:
        click.echo("Home Assistant context source stopped.")
    finally:
        store.close()


__all__ = ["context"]
