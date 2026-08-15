"""ChannelBridge — unified orchestrator for multi-channel messaging."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from openjarvis.channels._stubs import BaseChannel, ChannelStatus
from openjarvis.core.events import EventBus, EventType
from openjarvis.server.session_store import SessionStore

logger = logging.getLogger(__name__)

_DEFAULT_MAX_LENGTH = 4000
_SMS_MAX_LENGTH = 1600
_HOME_ASSISTANT_WRITE_ACTIONS = frozenset(
    {
        "turn_on",
        "turn_off",
        "toggle",
        "set_brightness",
        "set_color",
        "set_color_temperature",
        "set_temperature",
        "set_hvac_mode",
        "set_fan_speed",
        "set_volume",
        "set_cover_position",
        "open_cover",
        "close_cover",
        "lock",
        "unlock",
        "enable_automation",
        "disable_automation",
    }
)
_HOME_ASSISTANT_NOTIFICATION_DEBOUNCE_SECONDS = 5.0

_HELP_TEXT = """\
Available commands:
/agents — list running agents
/agent <id> status — agent state and current task
/agent <id> <message> — send a message to an agent
/agent <id> pause — pause an agent
/agent <id> resume — resume an agent
/notify <channel> — set where to receive notifications
/sessions — list your active sessions
/more — get the rest of a truncated response
/help — show this message\
"""

# Events the bridge subscribes to for notifications
_NOTIFICATION_EVENTS = [
    EventType.AGENT_TICK_END,
    EventType.AGENT_TICK_ERROR,
    EventType.AGENT_BUDGET_EXCEEDED,
    EventType.SCHEDULER_TASK_END,
]


class ChannelBridge:
    """Orchestrates incoming messages across multiple channel adapters.

    Provides backward-compatible ``send()``/``status()``/``list_channels()``
    so it can replace the old single-channel bridge in ``app.state``.
    """

    def __init__(
        self,
        channels: Dict[str, BaseChannel],
        session_store: SessionStore,
        bus: EventBus,
        system: Any = None,
        agent_manager: Any = None,
        deep_research_agent: Any = None,
    ) -> None:
        self._channels = channels
        self._session_store = session_store
        self._bus = bus
        self._system = system
        self._agent_manager = agent_manager
        self._deep_research_agent = deep_research_agent
        self._notification_timestamps: Dict[str, float] = {}
        self._subscribe_notifications()

    # --------------------------------------------------------------
    # Backward-compatible BaseChannel interface
    # --------------------------------------------------------------

    def connect(self) -> None:
        for ch in self._channels.values():
            ch.connect()

    def disconnect(self) -> None:
        for ch in self._channels.values():
            ch.disconnect()

    def list_channels(self) -> List[str]:
        result: List[str] = []
        for ch in self._channels.values():
            result.extend(ch.list_channels())
        return result

    def status(self) -> ChannelStatus:
        statuses = [ch.status() for ch in self._channels.values()]
        if not statuses:
            return ChannelStatus.DISCONNECTED
        if any(s == ChannelStatus.CONNECTED for s in statuses):
            return ChannelStatus.CONNECTED
        if all(s == ChannelStatus.ERROR for s in statuses):
            return ChannelStatus.ERROR
        return ChannelStatus.DISCONNECTED

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        target = str(channel or "").strip()
        for ch in self._channels.values():
            if target in ch.list_channels():
                return ch.send(
                    target,
                    content,
                    conversation_id=conversation_id,
                    metadata=metadata,
                )

        # ``channel_send`` receives the recipient identifier, while an
        # adapter's ``list_channels()`` returns its transport name.  SendBlue
        # therefore needs a small routing bridge for E.164 phone numbers:
        # ``+15551234567`` should use the ``sendblue`` adapter, not be looked
        # up as though it were an adapter name.
        if re.fullmatch(r"\+?\d{7,15}", target):
            sendblue = self._channels.get("sendblue")
            if sendblue is not None:
                return sendblue.send(
                    target,
                    content,
                    conversation_id=conversation_id,
                    metadata=metadata,
                )

        logger.warning("No adapter found for channel %s", target)
        return False

    # --------------------------------------------------------------
    # Incoming message handling
    # --------------------------------------------------------------

    def handle_incoming(
        self,
        sender_id: str,
        content: str,
        channel_type: str,
        metadata: Optional[Dict[str, Any]] = None,
        max_length: int = _DEFAULT_MAX_LENGTH,
    ) -> str:
        self._session_store.get_or_create(sender_id, channel_type)

        # Command routing
        stripped = content.strip()
        if stripped.startswith("/"):
            result = self._handle_command(sender_id, stripped, channel_type)
            if result is not None:
                return result

        # Regular chat — route to JarvisSystem.ask()
        return self._handle_chat(sender_id, stripped, channel_type, max_length)

    # --------------------------------------------------------------
    # Command parsing
    # --------------------------------------------------------------

    def _handle_command(
        self,
        sender_id: str,
        content: str,
        channel_type: str,
    ) -> Optional[str]:
        parts = content.split(None, 2)
        cmd = parts[0].lower()

        if cmd == "/help":
            return _HELP_TEXT

        if cmd == "/more":
            return self._handle_more(sender_id, channel_type)

        if cmd == "/notify" and len(parts) >= 2:
            pref = parts[1]
            self._session_store.set_notification_preference(
                sender_id, channel_type, pref
            )
            return f"Notifications will be sent to {pref}."

        if cmd == "/sessions":
            return self._handle_sessions(sender_id)

        if cmd == "/agents":
            return self._handle_agents_list()

        if cmd == "/agent" and len(parts) >= 2:
            agent_id = parts[1]
            rest = parts[2] if len(parts) > 2 else "status"
            return self._handle_agent_command(agent_id, rest)

        # Unknown command — fall through to chat
        return None

    def _handle_more(self, sender_id: str, channel_type: str) -> str:
        session = self._session_store.get_or_create(sender_id, channel_type)
        pending = session.get("pending_response")
        if pending:
            self._session_store.clear_pending_response(sender_id, channel_type)
            return pending
        return "No pending response."

    def _handle_agents_list(self) -> str:
        if not self._agent_manager:
            return "No agent manager configured."
        agents = self._agent_manager.list_agents()
        if not agents:
            return "No agents currently running."
        lines = []
        for a in agents:
            name = a.get("name", a.get("agent_id", "unknown"))
            status = a.get("status", "unknown")
            lines.append(f"  {name} — {status}")
        return "Running agents:\n" + "\n".join(lines)

    def _handle_agent_command(self, agent_id: str, action: str) -> str:
        if not self._agent_manager:
            return "No agent manager configured."
        action_lower = action.strip().lower()
        if action_lower == "status":
            state = self._agent_manager.get_agent(agent_id)
            if state is None:
                return f"Agent '{agent_id}' not found."
            name = state.get("name", agent_id)
            status = state.get("status", "unknown")
            return f"Agent '{name}': {status}"
        if action_lower == "pause":
            self._agent_manager.pause_agent(agent_id)
            return f"Agent '{agent_id}' paused."
        if action_lower == "resume":
            self._agent_manager.resume_agent(agent_id)
            return f"Agent '{agent_id}' resumed."
        # Treat as a message to the agent
        result = self._agent_manager.send_message(agent_id, action)
        return str(result) if result else f"Message sent to agent '{agent_id}'."

    # --------------------------------------------------------------
    # Chat handling
    # --------------------------------------------------------------

    def _handle_sessions(self, sender_id: str) -> str:
        targets = self._session_store.get_notification_targets()
        user_sessions = [t for t in targets if t["sender_id"] == sender_id]
        if not user_sessions:
            return "No active sessions with notification preferences."
        lines = []
        for s in user_sessions:
            lines.append(
                f"  {s['channel_type']} -> "
                f"notifications: {s['preferred_notification_channel']}"
            )
        return "Your sessions:\n" + "\n".join(lines)

    def _handle_chat(
        self,
        sender_id: str,
        content: str,
        channel_type: str,
        max_length: int,
    ) -> str:
        self._session_store.append_message(sender_id, channel_type, "user", content)
        session = self._session_store.get_or_create(sender_id, channel_type)
        history = session.get("conversation_history", [])

        home_response = self._handle_home_assistant_intent(
            content,
            history=history[:-1],
        )
        if home_response is not None:
            formatted = self._format_response(
                sender_id, channel_type, home_response, max_length
            )
            self._session_store.append_message(
                sender_id, channel_type, "assistant", home_response
            )
            return formatted

        # Build context from conversation history
        from openjarvis.core.types import Message, Role
        prior_msgs = []
        for msg in history[:-1]:  # exclude the message we just appended
            try:
                role = Role(msg['role'])
            except ValueError:
                role = Role.USER
            prior_msgs.append(Message(role=role, content=msg['content']))

        query = content

        # Try DeepResearchAgent first
        if self._deep_research_agent is not None:
            try:
                result = self._deep_research_agent.run(content)
                response_text = result.content or "No results found."
            except Exception as exc:
                logger.error("DeepResearch agent failed: %s", exc)
                response_text = f"Research error: {exc}"
        elif self._system is not None:
            try:
                agent_id = getattr(self._system, "agent_name", "orchestrator")
                if not agent_id or agent_id == "none":
                    agent_id = "orchestrator"
                    
                tool_names = None
                sys_prompt = None
                if self._agent_manager:
                    agent_data = self._agent_manager.get_agent(agent_id)
                    if agent_data and "config" in agent_data:
                        tool_names = agent_data["config"].get("tools")
                        if isinstance(tool_names, str):
                            tool_names = [t.strip() for t in tool_names.split(",") if t.strip()]
                        sys_prompt = agent_data["config"].get("system_prompt")

                result = self._system.ask(
                    query,
                    agent=agent_id,
                    tools=tool_names,
                    system_prompt=sys_prompt,
                    prior_messages=prior_msgs,
                )
                response_text = result.get("content", str(result))
            except Exception:
                logger.exception("Error in JarvisSystem.ask()")
                error_msg = (
                    "Sorry, I couldn't process that right now. Try again in a moment."
                )
                self._session_store.append_message(
                    sender_id, channel_type, "assistant", error_msg
                )
                return error_msg
        else:
            error_msg = (
                "Sorry, I couldn't process that right now. Try again in a moment."
            )
            self._session_store.append_message(
                sender_id, channel_type, "assistant", error_msg
            )
            return error_msg

        # Format and possibly truncate
        formatted = self._format_response(
            sender_id, channel_type, response_text, max_length
        )
        self._session_store.append_message(
            sender_id, channel_type, "assistant", response_text
        )
        return formatted

    def _handle_home_assistant_intent(
        self,
        content: str,
        *,
        history: List[Dict[str, str]] | None = None,
    ) -> Optional[str]:
        """Resolve natural Home Assistant speech before general chat.

        Live Home Assistant state supplies the entity map.  The behavior
        resolver then handles shorthand, follow-ups, and multi-domain intent
        selection; the verified tool remains the only component that performs
        a write.  If the live snapshot is unavailable, retain the small legacy
        shortcut so a connector outage does not break ordinary chat routing.
        """
        try:
            from openjarvis.behavior import BehaviorEntity, BehaviorResolver
            from openjarvis.tools.home_assistant import HomeAssistantTool

            tool = HomeAssistantTool()
            states = tool._get_states()
            entities = tuple(
                BehaviorEntity(
                    entity_id=str(state["entity_id"]),
                    name=str(
                        state.get("attributes", {}).get("friendly_name")
                        or state["entity_id"]
                    ),
                    domain=str(state["entity_id"]).split(".", 1)[0],
                    area=str(
                        state.get("attributes", {}).get("area_name")
                        or state.get("attributes", {}).get("area_id")
                        or ""
                    ),
                    aliases=tuple(
                        alias
                        for alias in state.get("attributes", {}).get("aliases", ())
                        if isinstance(alias, str)
                    ),
                )
                for state in states
                if isinstance(state, dict)
                and "." in str(state.get("entity_id") or "")
            )
            topic = self._recent_behavior_topic(history or [], entities)
            resolution = BehaviorResolver().resolve(
                content,
                entities=entities,
                recent_topic=topic,
            )
            if resolution.status == "clarify":
                return resolution.clarification or "Which device do you mean?"
            if resolution.status == "execute":
                tool_call = resolution.tool_call()
                if tool_call is not None:
                    result = tool.execute(**tool_call)
                    return result.content
        except Exception:
            logger.debug("Behavior-first Home Assistant routing unavailable", exc_info=True)

        return self._legacy_home_assistant_intent(content, history=history)

    @staticmethod
    def _recent_behavior_topic(
        history: List[Dict[str, str]],
        entities: tuple[Any, ...],
    ) -> Any | None:
        """Find the most recently named entity for pronoun resolution."""
        from openjarvis.behavior import BehaviorResolver
        from openjarvis.behavior.normalization import normalize_text

        for message in reversed(history[-6:]):
            text = normalize_text(str(message.get("content") or ""))
            explicit_entities = []
            padded = f" {text} "
            for entity in entities:
                aliases = (entity.name, entity.entity_id, *entity.aliases)
                if any(
                    f" {normalize_text(alias)} " in padded
                    for alias in aliases
                    if normalize_text(alias)
                ):
                    explicit_entities.append(entity)
            if len({entity.entity_id for entity in explicit_entities}) > 1:
                return None
            matches = BehaviorResolver._rank_entities(text, entities)
            if matches and matches[0].score >= 0.76:
                return matches[0].entity
        return None

    @staticmethod
    def _legacy_home_assistant_intent(
        content: str,
        *,
        history: List[Dict[str, str]] | None = None,
    ) -> Optional[str]:
        """Compatibility shortcut used when no live HA snapshot is available.

        These are the terse requests commonly sent over messaging.  Sending
        them through the concrete tool makes the answer state-backed and makes
        an action fail loudly when Home Assistant cannot verify it.
        """
        text = content.lower().strip()
        try:
            from openjarvis.tools.home_assistant import HomeAssistantTool

            tool = HomeAssistantTool()
            if "temperature" in text and not re.search(r"\b(weather|outside|forecast)\b", text):
                result = tool.execute(action="get_temperature")
                return result.content

            explicit_lamp = bool(
                re.search(r"\bliving\s+room\s+(lamp|light)\b", text)
            )
            requested_power = re.search(
                r"\b(?:turn|switch|set|make)\b.*\b(on|off)\b", text
            )
            requested_status = bool(
                re.search(r"\b(status|state)\b", text)
                or re.search(r"\b(?:is|was)\s+(?:it|that)\s+(?:on|off)\b", text)
            )
            refers_to_lamp = explicit_lamp or (
                bool(requested_power or requested_status)
                and ChannelBridge._recently_discussed_living_room_lamp(history or [])
            )
            if not refers_to_lamp:
                return None
            entity = "living room lamp"
            if requested_power and requested_power.group(1) == "off":
                result = tool.execute(action="turn_off", entity=entity)
                return result.content
            if requested_power and requested_power.group(1) == "on":
                result = tool.execute(action="turn_on", entity=entity)
                return result.content
            if requested_status or explicit_lamp:
                result = tool.execute(action="get_state", entity=entity)
                return result.content
        except Exception:
            logger.exception("Direct Home Assistant message handling failed")
            return "I couldn't reach Home Assistant right now, so I can't confirm the device state."
        return None

    @staticmethod
    def _recently_discussed_living_room_lamp(
        history: List[Dict[str, str]],
    ) -> bool:
        """Resolve ``it`` only when the recent conversation names this lamp."""
        recent_text = " ".join(
            str(message.get("content") or "").lower()
            for message in history[-6:]
        )
        return bool(re.search(r"\bliving\s+room\s+(lamp|light)\b", recent_text))

    def _format_response(
        self,
        sender_id: str,
        channel_type: str,
        response: str,
        max_length: int,
    ) -> str:
        if len(response) <= max_length:
            return response
        # Truncate and store full response for /more retrieval
        truncation_notice = "\n\n... (reply /more for full response)"
        cut_at = max_length - len(truncation_notice)
        truncated = response[:cut_at] + truncation_notice
        self._session_store.set_pending_response(sender_id, channel_type, response)
        return truncated

    # --------------------------------------------------------------
    # Notifications
    # --------------------------------------------------------------

    def _subscribe_notifications(self) -> None:
        for event_type in _NOTIFICATION_EVENTS:
            self._bus.subscribe(event_type, self._on_notification_event)
        self._bus.subscribe(
            EventType.TOOL_CALL_END, self._on_home_assistant_tool_event
        )

    def _on_home_assistant_tool_event(self, event) -> None:  # noqa: ANN001
        """Send immediate updates for verified Home Assistant write actions."""
        data = event.data or {}
        if data.get("tool") != "home_assistant" or not data.get("success"):
            return

        metadata = data.get("metadata") or {}
        arguments = metadata.get("arguments") or data.get("arguments") or {}
        if not isinstance(arguments, dict):
            return
        action = str(arguments.get("action") or "").strip().lower()
        if action not in _HOME_ASSISTANT_WRITE_ACTIONS:
            return

        entity = str(arguments.get("entity") or "device").strip().lower()
        agent_id = str(data.get("agent") or data.get("agent_id") or "unknown")
        event_key = f"home_assistant:{agent_id}:{action}:{entity}"
        now = time.time()
        last = self._notification_timestamps.get(event_key, 0)
        if now - last < _HOME_ASSISTANT_NOTIFICATION_DEBOUNCE_SECONDS:
            return
        self._notification_timestamps[event_key] = now

        result = str(data.get("result") or "").strip()
        if not result:
            return
        actor = self._agent_name(data)
        self._send_to_notification_targets(f"{actor}: {result}")

    def _agent_name(self, data: Dict[str, Any]) -> str:
        """Resolve a human-friendly agent name for an action notification."""
        explicit_name = str(data.get("agent_name") or "").strip()
        if explicit_name:
            return explicit_name

        agent_id = str(data.get("agent") or data.get("agent_id") or "").strip()
        if self._agent_manager is not None and agent_id:
            try:
                agent = self._agent_manager.get_agent(agent_id)
                if agent:
                    name = str(agent.get("name") or "").strip()
                    if name:
                        return name
            except Exception:
                logger.debug(
                    "Could not resolve agent name for notification",
                    exc_info=True,
                )
        return "Ophanim"

    def _on_notification_event(self, event) -> None:  # noqa: ANN001
        event_key = str(event.event_type)
        now = time.time()

        # Rate limit: max 1 per event type per 5 minutes
        last = self._notification_timestamps.get(event_key, 0)
        if now - last < 300:
            return
        self._notification_timestamps[event_key] = now

        message = self._format_notification(event)
        if not message:
            return

        self._send_to_notification_targets(message)

    def _send_to_notification_targets(self, message: str) -> None:
        targets = self._session_store.get_notification_targets()
        for target in targets:
            pref_channel = target["preferred_notification_channel"]
            sender_id = target["sender_id"]
            self._send_notification(pref_channel, sender_id, message)

    def _format_notification(  # noqa: ANN201
        self,
        event,  # noqa: ANN001
    ) -> Optional[str]:
        data = event.data or {}
        name = data.get("agent_name", data.get("name", "unknown"))

        if event.event_type == EventType.AGENT_TICK_END:
            summary = data.get("summary", data.get("result", ""))
            return f"Agent '{name}' finished: {summary}" if summary else None
        if event.event_type == EventType.AGENT_TICK_ERROR:
            error = data.get("error", "unknown error")
            return f"Agent '{name}' error: {error}"
        if event.event_type == EventType.AGENT_BUDGET_EXCEEDED:
            return f"Agent '{name}' hit budget limit."
        if event.event_type == EventType.SCHEDULER_TASK_END:
            if data.get("success", True):
                return f"Scheduled task '{name}' completed."
            error = data.get("error", "unknown error")
            return f"Scheduled task '{name}' failed: {error}"
        return None

    def _send_notification(
        self,
        channel_type: str,
        sender_id: str,
        message: str,
    ) -> None:
        ch = self._channels.get(channel_type)
        if ch is None:
            logger.warning(
                "No adapter for notification channel %s",
                channel_type,
            )
            return
        try:
            ch.send(sender_id, message)
        except Exception:
            logger.exception("Failed to send notification to %s", channel_type)
