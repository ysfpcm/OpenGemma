import { useCallback, useEffect, useRef, useState } from 'react';
import { getApiKey, getBase } from './api';
import type { SystemEvent, SystemEventConnection } from '../types/operations';

const MAX_EVENTS = 2_000;

function eventKey(event: SystemEvent): string {
  return event.id != null
    ? `id:${event.id}`
    : `${event.timestamp}:${event.type}:${event.summary}`;
}

function buildSystemEventsUrl(): string {
  const configured = getBase().replace(/\/v1\/?$/, '');
  const origin = configured
    ? configured.replace(/^http/, 'ws')
    : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
  const token = getApiKey();
  return `${origin}/v1/system/events${token ? `?token=${encodeURIComponent(token)}` : ''}`;
}

export function useSystemEvents() {
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [connection, setConnection] = useState<SystemEventConnection>('connecting');
  const retryRef = useRef(0);
  const onMessageRef = useRef<(event: SystemEvent) => void>(() => {});

  const clearEvents = useCallback(() => setEvents([]), []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let closed = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleReconnect = () => {
      if (closed) return;
      setConnection('disconnected');
      const delay = Math.min(30_000, 1_000 * 2 ** Math.min(retryRef.current, 5));
      retryRef.current += 1;
      retryTimer = setTimeout(connect, delay);
    };

    const connect = () => {
      if (closed) return;
      setConnection('connecting');
      try {
        socket = new WebSocket(buildSystemEventsUrl());
      } catch {
        scheduleReconnect();
        return;
      }

      socket.onopen = () => {
        retryRef.current = 0;
        setConnection('connected');
      };
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as SystemEvent;
          if (!event?.type || !event?.summary) return;
          setEvents((current) => {
            const next = current.filter((candidate) => eventKey(candidate) !== eventKey(event));
            return [...next, event].slice(-MAX_EVENTS);
          });
          onMessageRef.current(event);
        } catch {
          // Ignore malformed server messages.
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = scheduleReconnect;
    };

    connect();
    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  return { events, connection, clearEvents };
}
