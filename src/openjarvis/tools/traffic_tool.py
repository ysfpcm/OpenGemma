"""Traffic lookup tool using Google Maps with Web Search fallback."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.web_search import WebSearchTool

logger = logging.getLogger(__name__)


@ToolRegistry.register("traffic_lookup")
class TrafficTool(BaseTool):
    """Lookup traffic and travel time."""

    tool_id = "traffic_lookup"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="traffic_lookup",
            description="Lookup estimated travel time and traffic conditions between an origin and destination.",
            parameters={
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "string",
                        "description": "Starting location (e.g., 'San Francisco, CA' or '123 Main St, NY').",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Ending location.",
                    },
                    "departure_time": {
                        "type": "string",
                        "description": (
                            "Optional local ISO-8601 departure time. Defaults to now "
                            "for a live traffic lookup."
                        ),
                    },
                },
                "required": ["origin", "destination"],
            },
            category="search",
        )

    def execute(self, **params: Any) -> ToolResult:
        origin = params.get("origin", "")
        destination = params.get("destination", "")
        departure_time = params.get("departure_time")

        if not origin or not destination:
            return ToolResult(
                tool_name="traffic_lookup",
                content="Both origin and destination must be provided.",
                success=False,
            )

        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")

        departure_param: str | int = "now"
        if departure_time:
            try:
                parsed_departure = datetime.fromisoformat(str(departure_time))
                if parsed_departure.tzinfo is None:
                    local_timezone = datetime.now().astimezone().tzinfo
                    parsed_departure = parsed_departure.replace(
                        tzinfo=local_timezone
                    )
                departure_param = int(parsed_departure.timestamp())
            except (TypeError, ValueError, OverflowError):
                return ToolResult(
                    tool_name="traffic_lookup",
                    content="departure_time must be a valid ISO-8601 timestamp.",
                    success=False,
                    metadata={"provider_confidence": "invalid_input"},
                )

        if api_key:
            try:
                url = "https://maps.googleapis.com/maps/api/distancematrix/json"
                resp = httpx.get(
                    url,
                    params={
                        "origins": origin,
                        "destinations": destination,
                        "departure_time": departure_param,
                        "key": api_key,
                    },
                    timeout=15.0,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("status") == "OK":
                    element = data["rows"][0]["elements"][0]
                    if element.get("status") == "OK":
                        distance = element["distance"]["text"]
                        duration = element["duration"]["text"]
                        duration_in_traffic_data = element.get("duration_in_traffic", {})
                        duration_in_traffic = duration_in_traffic_data.get("text", "Unknown")

                        content = (
                            f"Traffic Report: {origin} to {destination}\n"
                            f"Distance: {distance}\n"
                            f"Normal Duration: {duration}\n"
                            f"Duration with Traffic: {duration_in_traffic}"
                        )
                        return ToolResult(
                            tool_name="traffic_lookup",
                            content=content,
                            success=True,
                            metadata={
                                "provider": "google_maps",
                                "distance": distance,
                                "normal_duration_seconds": element["duration"].get("value"),
                                "traffic_duration_seconds": duration_in_traffic_data.get("value"),
                                "departure_time": departure_time or "now",
                            }
                        )
            except Exception as e:
                logger.error("Error looking up traffic with Google Maps: %s", e)
                # Fallback to web search if API fails

        # Fallback: Use WebSearchTool
        try:
            search_tool = WebSearchTool(max_results=3)
            query = f"current traffic conditions from {origin} to {destination}"
            search_result = search_tool.execute(query=query)

            if search_result.success:
                content = (
                    f"Traffic Report (via Web Search): {origin} to {destination}\n\n"
                    f"{search_result.content}\n\n"
                    "(Note: Set GOOGLE_MAPS_API_KEY environment variable for precise ETA data.)"
                )
                return ToolResult(
                    tool_name="traffic_lookup",
                    content=content,
                    success=True,
                    metadata={"provider": "web_search_fallback"}
                )
            else:
                return search_result
        except Exception as e:
            logger.error("Error in traffic fallback search: %s", e)
            return ToolResult(
                tool_name="traffic_lookup",
                content=f"Error looking up traffic: {e}",
                success=False,
            )

__all__ = ["TrafficTool"]
