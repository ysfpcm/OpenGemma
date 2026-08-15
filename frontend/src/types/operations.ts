import type { ContextSnapshot } from './index';

export type ConsoleEventCategory =
  | 'inference'
  | 'tools'
  | 'context'
  | 'cameras'
  | 'channels'
  | 'traces'
  | 'agents'
  | 'security'
  | 'system';

export type SystemEventCategory = ConsoleEventCategory;

export type ConsoleEventLevel = 'info' | 'warn' | 'error';

export interface SystemEvent {
  id?: number;
  type: string;
  timestamp: number;
  category: ConsoleEventCategory;
  level: ConsoleEventLevel;
  summary: string;
  data: Record<string, string | number | boolean>;
}

export type SystemEventConnection = 'connecting' | 'connected' | 'disconnected';

export interface ContextSchemaColumn {
  name: string;
  type: string;
  key?: string;
}

export interface ContextSchemaTable {
  name: string;
  purpose: string;
  columns: ContextSchemaColumn[];
  relationships: string[];
}

export interface ContextInspect {
  database: string;
  generated_at: string;
  schema: ContextSchemaTable[];
  sources: Array<{
    source_key: string;
    display_name: string;
    status: string;
    stale_after_seconds: number;
    last_seen_at: string | null;
    last_sync_at: string | null;
  }>;
  entities: ContextSnapshot['entities'];
  current_state: Array<{
    entity_id: number;
    source_key: string;
    entity_name: string;
    state_key: string;
    value: unknown;
    unit: string;
    quality: string;
    observed_at: string;
  }>;
  recent_events: ContextSnapshot['recent_events'];
  state_history: Array<{
    id: number;
    entity_id: number;
    state_key: string;
    previous_value: unknown;
    new_value: unknown;
    unit: string;
    event_id: number | null;
    occurred_at: string;
    recorded_at: string;
  }>;
  snapshots: Array<{
    id: number;
    source_key: string;
    entity_name: string | null;
    event_id: number | null;
    snapshot_id: string;
    local_ref: string;
    status: 'received' | 'failed' | string;
    content_type: string | null;
    byte_size: number | null;
    sha256: string | null;
    error_code: string | null;
    captured_at: string;
    created_at: string;
    metadata: Record<string, unknown>;
  }>;
  warnings: string[];
}
