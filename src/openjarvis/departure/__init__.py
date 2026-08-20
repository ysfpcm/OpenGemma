"""Phase 7 Departure Guardian contracts, live adapters, and orchestration."""

from .adapters import (
    FakeHomeAssistantAdapter,
    FakeNotificationAdapter,
    HomeAssistantAdapter,
    LiveHomeAssistantAdapter,
    LiveNotificationAdapter,
    NotificationAdapter,
    register_departure_adapters,
)
from .models import (
    CalendarSource,
    DepartureAction,
    DepartureObservations,
    DeparturePolicy,
    DepartureSetup,
    DestinationAssumptions,
    NotificationChannel,
    PreparationBuffer,
    PresenceSource,
    RecalculationResult,
    SourceStatus,
    TrafficSource,
    WeatherSource,
    WhyNowBrief,
)
from .service import DepartureGuardian, Phase7Flags
from .store import DepartureStore
from .watcher import DepartureWatcherService

__all__ = [
    "CalendarSource",
    "DepartureAction",
    "DepartureGuardian",
    "DepartureObservations",
    "DeparturePolicy",
    "DepartureSetup",
    "DepartureStore",
    "DepartureWatcherService",
    "DestinationAssumptions",
    "FakeHomeAssistantAdapter",
    "FakeNotificationAdapter",
    "HomeAssistantAdapter",
    "LiveHomeAssistantAdapter",
    "LiveNotificationAdapter",
    "NotificationChannel",
    "NotificationAdapter",
    "Phase7Flags",
    "PreparationBuffer",
    "PresenceSource",
    "RecalculationResult",
    "SourceStatus",
    "TrafficSource",
    "WeatherSource",
    "WhyNowBrief",
    "register_departure_adapters",
]
