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

export interface CodexMission {
  id: string;
  objective: string;
  workspace: string;
  thread_id: string | null;
  active_turn_id: string | null;
  status: string;
  phase: string;
  progress: string;
  plan: unknown[];
  commands: Array<Record<string, unknown>>;
  tools: Array<Record<string, unknown>>;
  files: Array<Record<string, unknown>>;
  verification_state: string;
  errors: Array<Record<string, unknown>>;
  usage: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  last_meaningful_at: string;
  interrupted_reason: string | null;
  mode?: 'read-only' | 'workspace-write' | string;
  budgets?: Record<string, number>;
  budget_state?: {
    used?: Record<string, number>;
    limits?: Record<string, number>;
    exceeded?: string[];
  };
  authority?: {
    sandbox?: string;
    approval_policy?: string;
    network_access?: boolean;
    writable_roots?: string[];
  };
  effects?: Array<Record<string, unknown>>;
  parent_mission_id?: string | null;
  selected_fork_id?: string | null;
  controls?: Array<{ action: string; detail: Record<string, unknown>; recorded_at: string }>;
  decisions?: Array<{
    id: string;
    request_method: string;
    kind: string;
    status: string;
    offered: { decision_values?: string[]; question_ids?: string[] };
    requested: Record<string, unknown>;
    guardian_allowed: boolean;
    guardian_allows: boolean;
    guardian_reason: string;
    codex_requested: boolean;
    marc_approved: boolean;
    marc_decision: Record<string, unknown> | null;
  }>;
  milestones?: Array<{ summary: string; event_fingerprint: string; recorded_at: string }>;
  events?: Array<{ id: number; fingerprint: string; method: string; display: Record<string, unknown>; recorded_at: string }>;
}

export interface GuardianTimeline {
  proposal: Record<string, unknown>;
  state: string;
  attempts: Array<Record<string, unknown>>;
  verifications: Array<Record<string, unknown>>;
  action_audit: Array<Record<string, unknown>>;
  guardian_audit: Array<Record<string, unknown>>;
}

export interface ShadowSituation {
  id: string;
  situation_type: string;
  status: 'active' | 'uncertain' | 'closed' | string;
  confidence: number | null;
  evidence_ids: string[];
  uncertainty: string[];
  provenance: Record<string, unknown>;
  source_provenance: Record<string, unknown>;
  created_at: string;
  valid_from: string;
  valid_until: string | null;
}

export interface ShadowSituationView {
  mode: 'shadow' | string;
  situations: ShadowSituation[];
  evaluations: Array<Record<string, unknown>>;
  dead_letters: Array<Record<string, unknown>>;
  side_effects: false;
}
