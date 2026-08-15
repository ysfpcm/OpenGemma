import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  CircleDashed,
  Clock3,
  Database,
  DatabaseZap,
  Eye,
  GitBranch,
  History,
  House,
  Info,
  Mail,
  RefreshCw,
  Route,
  Search,
  Server,
  ShieldCheck,
  Table2,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { fetchContextInspect } from '../../lib/api';
import type { ContextInspect, SystemEvent } from '../../types/operations';

type AtlasMode = 'topology' | 'database';
type EntityFilter = 'all' | 'fresh' | 'stale';

const sourceIcons: Record<string, typeof Server> = {
  traffic: Route,
  home_assistant: House,
  gmail: Mail,
};

function displayValue(value: unknown, maxLength = 64): string {
  if (value === null || value === undefined) return '—';
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function statusColor(status: string): string {
  if (status === 'online' || status === 'good') return '#4ade80';
  if (status === 'degraded' || status === 'stale' || status === 'unknown') return '#fbbf24';
  if (status === 'offline' || status === 'error') return '#fb7185';
  return '#94a3b8';
}

function formatRelative(value: string | null): string {
  if (!value) return 'No observation yet';
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return 'Unknown time';
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 45) return 'Just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} hr ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

function StatusPill({ status, label }: { status: string; label?: string }) {
  const color = statusColor(status);
  const Icon = status === 'offline' ? WifiOff : status === 'online' || status === 'good' ? Wifi : Clock3;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[10px] font-medium" style={{ borderColor: `${color}35`, color, background: `${color}0d` }}>
      <Icon size={11} /> {label ?? status}
    </span>
  );
}

function Metric({ label, value, detail, tone = 'text-slate-100', icon: Icon }: { label: string; value: string | number; detail?: string; tone?: string; icon: typeof Activity }) {
  return (
    <div className="rounded-xl border border-slate-800/90 bg-slate-950/45 p-3">
      <div className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-[0.18em] text-slate-500"><span>{label}</span><Icon size={14} className="text-slate-600" /></div>
      <div className={`mt-2 text-2xl font-semibold tracking-tight ${tone}`}>{value}</div>
      {detail && <div className="mt-1 text-[10px] text-slate-500">{detail}</div>}
    </div>
  );
}

function sourceFreshness(source: ContextInspect['sources'][number]): { label: string; status: string } {
  if (source.status === 'offline') return { label: 'Offline', status: 'offline' };
  if (source.status === 'degraded') return { label: 'Needs attention', status: 'degraded' };
  if (!source.last_seen_at) return { label: 'Waiting for first sync', status: 'unknown' };
  const age = (Date.now() - new Date(source.last_seen_at).getTime()) / 1000;
  if (age > source.stale_after_seconds) return { label: 'No recent update', status: 'stale' };
  return { label: 'Receiving updates', status: 'online' };
}

function SourceCard({ source, entityCount, staleCount }: { source: ContextInspect['sources'][number]; entityCount: number; staleCount: number }) {
  const Icon = sourceIcons[source.source_key] ?? Server;
  const freshness = sourceFreshness(source);
  const isTraffic = source.source_key === 'traffic';
  return (
    <div className={`rounded-2xl border p-4 transition-colors ${isTraffic ? 'border-emerald-400/25 bg-emerald-400/[0.055]' : 'border-slate-800 bg-slate-950/35'}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border ${isTraffic ? 'border-emerald-300/25 bg-emerald-300/10 text-emerald-200' : 'border-cyan-300/20 bg-cyan-300/10 text-cyan-200'}`}><Icon size={18} /></div>
          <div className="min-w-0"><div className="truncate text-sm font-semibold text-slate-100">{source.display_name}</div><div className="mt-0.5 truncate font-mono text-[10px] text-slate-500">{source.source_key}</div></div>
        </div>
        <StatusPill status={freshness.status} label={freshness.label} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 border-t border-slate-800/80 pt-3">
        <div><div className="text-[10px] text-slate-500">Entities</div><div className="mt-1 text-sm font-medium text-slate-200">{entityCount}</div></div>
        <div><div className="text-[10px] text-slate-500">Last seen</div><div className="mt-1 text-sm font-medium text-slate-200">{formatRelative(source.last_seen_at)}</div></div>
      </div>
      <div className="mt-3 flex items-center gap-2 text-[10px] text-slate-500">
        {staleCount > 0 ? <><AlertTriangle size={12} className="text-amber-300" /> {staleCount} entity{staleCount === 1 ? '' : 'ies'} quiet longer than its freshness window</> : <><CheckCircle2 size={12} className="text-emerald-300" /> All entities are within their freshness window</>}
      </div>
    </div>
  );
}

function DataFlow() {
  const steps = [
    { label: 'CONNECTED SOURCES', detail: 'Traffic, Home Assistant, email…', icon: Server, color: 'text-cyan-200' },
    { label: 'LOCAL MEMORY', detail: 'SQLite latest state + history', icon: DatabaseZap, color: 'text-emerald-200' },
    { label: 'LOCAL MODEL', detail: 'Queries the freshest useful facts', icon: BrainCircuit, color: 'text-violet-200' },
  ];
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-950/35 p-4">
      <div className="flex items-center gap-2 text-xs font-semibold text-slate-200"><GitBranch size={15} className="text-cyan-200" /> How your context moves</div>
      <div className="mt-3 grid gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-center">
        {steps.map((step, index) => <div key={step.label} className="contents"><div className="rounded-xl border border-slate-800 bg-black/20 p-3"><div className={`flex items-center gap-2 text-[10px] font-semibold tracking-[0.12em] ${step.color}`}><step.icon size={14} /> {step.label}</div><div className="mt-1 text-[11px] text-slate-500">{step.detail}</div></div>{index < steps.length - 1 && <span className="hidden text-slate-600 md:block">→</span>}</div>)}
      </div>
    </div>
  );
}

function EntityMemory({ inspect }: { inspect: ContextInspect }) {
  const [filter, setFilter] = useState<EntityFilter>('all');
  const [query, setQuery] = useState('');
  const filteredEntities = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return inspect.entities.filter((entity) => {
      const matchesFilter = filter === 'all' || (filter === 'stale' ? entity.stale : !entity.stale);
      const matchesQuery = !normalized || `${entity.entity_name} ${entity.source_key} ${entity.entity_type} ${entity.area}`.toLowerCase().includes(normalized);
      return matchesFilter && matchesQuery;
    });
  }, [filter, inspect.entities, query]);

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-950/35 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><div className="flex items-center gap-2 text-xs font-semibold text-slate-200"><Eye size={15} className="text-emerald-200" /> What the model currently knows</div><p className="mt-1 text-[11px] text-slate-500">These are the latest materialized facts available before a live tool call is needed.</p></div>
        <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-black/20 p-1" role="group" aria-label="Entity freshness filter">
          {(['all', 'fresh', 'stale'] as EntityFilter[]).map((option) => <button key={option} type="button" aria-pressed={filter === option} onClick={() => setFilter(option)} className={`rounded-md px-2.5 py-1.5 text-[10px] capitalize transition-colors ${filter === option ? 'bg-cyan-400/10 text-cyan-100' : 'text-slate-500 hover:text-slate-300'}`}>{option}</button>)}
        </div>
      </div>
      <label className="mt-3 flex items-center gap-2 rounded-lg border border-slate-800 bg-black/20 px-3 py-2 text-slate-500"><Search size={13} /><span className="sr-only">Search entities</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search entities, sources, or areas" className="w-full bg-transparent text-xs text-slate-200 outline-none placeholder:text-slate-600" /></label>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {filteredEntities.slice(0, 18).map((entity) => {
          const summary = entity.states.find((state) => ['state', 'traffic_duration', 'temperature', 'status', 'motion'].includes(state.state_key)) ?? entity.states[0];
          return <div key={entity.entity_id} className="rounded-xl border border-slate-800/90 bg-black/20 p-3"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><div className="truncate text-xs font-medium text-slate-200">{entity.entity_name}</div><div className="mt-1 truncate text-[10px] text-slate-500">{entity.source_key}{entity.area ? ` · ${entity.area}` : ''}</div></div><span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${entity.stale ? 'bg-amber-300 shadow-[0_0_8px_rgba(251,191,36,0.8)]' : 'bg-emerald-300 shadow-[0_0_8px_rgba(74,222,128,0.8)]'}`} title={entity.stale ? 'Stale' : 'Fresh'} /> </div><div className="mt-3 flex items-baseline justify-between gap-2 border-t border-slate-800/70 pt-2"><span className="truncate font-mono text-[10px] text-slate-600">{summary?.state_key ?? 'No state'}</span><span className="truncate text-right text-xs text-slate-200">{summary ? `${displayValue(summary.value)}${summary.unit ? ` ${summary.unit}` : ''}` : '—'}</span></div></div>;
        })}
      </div>
      {filteredEntities.length > 18 && <div className="mt-3 text-center text-[10px] text-slate-600">Showing 18 of {filteredEntities.length} matching entities. Use Database for the complete record.</div>}
      {filteredEntities.length === 0 && <div className="rounded-xl border border-dashed border-slate-800 px-3 py-8 text-center text-xs text-slate-600">No entities match this view.</div>}
    </section>
  );
}

function RecentEvents({ inspect }: { inspect: ContextInspect }) {
  return <section className="rounded-2xl border border-slate-800 bg-slate-950/35 p-4"><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2 text-xs font-semibold text-slate-200"><History size={15} className="text-amber-200" /> Recent memory activity</div><span className="text-[10px] text-slate-500">{inspect.recent_events.length} loaded</span></div><div className="mt-3 space-y-2">{inspect.recent_events.slice(0, 6).map((event) => <div key={event.event_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800/80 bg-black/20 px-3 py-2"><div className="min-w-0"><div className="truncate text-[11px] text-slate-300">{event.entity_name ?? event.source_key} <span className="text-slate-600">· {event.event_type.split('_').join(' ')}</span></div><div className="mt-0.5 text-[10px] text-slate-600">{event.source_key}</div></div><time className="shrink-0 font-mono text-[10px] text-slate-600">{formatRelative(event.occurred_at)}</time></div>)}{inspect.recent_events.length === 0 && <p className="text-xs text-slate-600">No context events recorded yet.</p>}</div></section>;
}

function rowsForTable(inspect: ContextInspect, tableName: string): Array<Record<string, unknown>> {
  if (tableName === 'context_sources') return inspect.sources as Array<Record<string, unknown>>;
  if (tableName === 'context_entities') return inspect.entities.map((entity) => ({ id: entity.entity_id, source_id: entity.source_key, external_id: entity.external_entity_id, entity_type: entity.entity_type, display_name: entity.entity_name, area: entity.area, stale: entity.stale }));
  if (tableName === 'context_current_state') return inspect.current_state as Array<Record<string, unknown>>;
  if (tableName === 'context_events') return inspect.recent_events.map((event) => ({ id: event.event_id, source_id: event.source_key, entity_id: event.entity_name, event_type: event.event_type, occurred_at: event.occurred_at, payload_json: event.payload }));
  if (tableName === 'context_snapshots') return inspect.snapshots.map((snapshot) => ({ ...snapshot, source_id: snapshot.source_key, entity_id: snapshot.entity_name, metadata_json: snapshot.metadata }));
  return inspect.state_history as Array<Record<string, unknown>>;
}

function DatabaseView({ inspect }: { inspect: ContextInspect }) {
  const [selectedTable, setSelectedTable] = useState('context_sources');
  const table = inspect.schema.find((item) => item.name === selectedTable) ?? inspect.schema[0];
  const rows = rowsForTable(inspect, table?.name ?? selectedTable);
  return <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-hidden lg:grid-cols-[220px_1fr]"><div className="min-h-0 overflow-y-auto rounded-2xl border border-slate-800 bg-slate-950/35 p-2"><div className="mb-2 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">Database tables</div><div className="space-y-1">{inspect.schema.map((item) => { const count = rowsForTable(inspect, item.name).length; return <button key={item.name} type="button" onClick={() => setSelectedTable(item.name)} className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2.5 text-left transition-colors ${selectedTable === item.name ? 'bg-cyan-400/10 text-cyan-100' : 'text-slate-500 hover:bg-slate-900/60 hover:text-slate-300'}`}><span className="flex min-w-0 items-center gap-2"><Table2 size={13} /><span className="truncate text-[11px]">{item.name.replace('context_', '').split('_').join(' ')}</span></span><span className="font-mono text-[9px] text-slate-600">{count}</span></button>; })}</div></div><div className="min-h-0 overflow-y-auto rounded-2xl border border-slate-800 bg-slate-950/35 p-4">{table && <><div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 pb-3"><div><div className="flex items-center gap-2 text-sm font-semibold text-cyan-100"><Table2 size={15} /> {table.name.replace('context_', '').split('_').join(' ')}</div><p className="mt-1 max-w-2xl text-[11px] leading-relaxed text-slate-500">{table.purpose}</p></div><span className="rounded-full border border-slate-800 px-2 py-1 font-mono text-[10px] text-slate-500">{rows.length} rows</span></div><div className="mt-3 overflow-x-auto"><table className="w-full min-w-[600px] border-collapse text-[10px]"><thead><tr>{table.columns.map((column) => <th key={column.name} className="border-b border-slate-800 px-2 py-2 text-left font-medium uppercase tracking-wider text-slate-600">{column.name}{column.key ? <span className="ml-1 text-cyan-500">{column.key}</span> : null}</th>)}</tr></thead><tbody>{rows.slice(0, 100).map((row, rowIndex) => <tr key={`${table.name}-${rowIndex}`} className="border-b border-slate-900/80 hover:bg-slate-900/40">{table.columns.map((column) => <td key={column.name} className="max-w-[220px] truncate px-2 py-2 text-slate-300" title={displayValue(row[column.name], 240)}>{displayValue(row[column.name])}</td>)}</tr>)}{rows.length === 0 && <tr><td colSpan={table.columns.length} className="px-2 py-10 text-center text-slate-600">No rows available in this table yet.</td></tr>}</tbody></table></div>{table.relationships.length > 0 && <div className="mt-4 rounded-xl border border-slate-800 bg-black/20 p-3"><div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500"><GitBranch size={12} /> How this table connects</div><div className="flex flex-wrap gap-2">{table.relationships.map((relationship) => <span key={relationship} className="rounded-full bg-slate-900 px-2.5 py-1 text-[10px] text-slate-400">{relationship}</span>)}</div></div>}</>}</div></div>;
}

function Overview({ inspect }: { inspect: ContextInspect }) {
  const entitiesBySource = useMemo(() => new Map(inspect.sources.map((source) => [source.source_key, inspect.entities.filter((entity) => entity.source_key === source.source_key)])), [inspect.entities, inspect.sources]);
  const freshEntities = inspect.entities.filter((entity) => !entity.stale).length;
  const staleEntities = inspect.entities.length - freshEntities;
  const onlineSources = inspect.sources.filter((source) => sourceFreshness(source).status === 'online').length;
  const syncValues = inspect.sources.map((source) => source.last_sync_at || source.last_seen_at).filter((value): value is string => Boolean(value)).sort();
  const lastSync = syncValues.length ? syncValues[syncValues.length - 1] : null;
  return <div className="space-y-3 pr-1"><div className="grid grid-cols-2 gap-2 xl:grid-cols-4"><Metric label="Connected sources" value={onlineSources} detail={`of ${inspect.sources.length} receiving updates`} tone="text-cyan-100" icon={Server} /><Metric label="Fresh entities" value={freshEntities} detail={staleEntities ? `${staleEntities} need a closer look` : 'Everything is current'} tone="text-emerald-200" icon={CheckCircle2} /><Metric label="Quiet entities" value={staleEntities} detail="Outside freshness window" tone={staleEntities ? 'text-amber-200' : 'text-slate-100'} icon={Clock3} /><Metric label="Memory refreshed" value={formatRelative(lastSync)} detail="Latest source sync" tone="text-violet-200" icon={RefreshCw} /></div><div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.045] px-3 py-2.5"><div className="flex items-start gap-2 text-[11px] leading-relaxed text-amber-100"><Info size={14} className="mt-0.5 shrink-0 text-amber-300" /><span><strong className="font-semibold">How to read “quiet”:</strong> it means this entity has not reported a new observation inside its configured freshness window. It is not automatically broken or disabled; many backup and routine entities are naturally quiet.</span></div></div><div className="grid gap-3 xl:grid-cols-2">{inspect.sources.map((source) => <SourceCard key={source.source_key} source={source} entityCount={entitiesBySource.get(source.source_key)?.length ?? 0} staleCount={entitiesBySource.get(source.source_key)?.filter((entity) => entity.stale).length ?? 0} />)}</div><DataFlow /><EntityMemory inspect={inspect} /><RecentEvents inspect={inspect} /></div>;
}

export function ContextAtlas({ events }: { events: SystemEvent[] }) {
  const [mode, setMode] = useState<AtlasMode>('topology');
  const [inspect, setInspect] = useState<ContextInspect | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<number | null>(null);
  const contextLedger = [...events].reverse().find((event) => event.type === 'context_assembled');

  const refreshInspect = useCallback(async () => {
    setRefreshing(true);
    const next = await fetchContextInspect({ recentEventLimit: 40, historyLimit: 100 });
    if (next) { setInspect(next); setLastRefresh(Date.now()); }
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void refreshInspect();
    const timer = window.setInterval(() => void refreshInspect(), 5000);
    return () => window.clearInterval(timer);
  }, [refreshInspect]);

  return <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-800 bg-[#080f1b] shadow-[0_20px_60px_rgba(0,0,0,0.22)]"><div className="shrink-0 border-b border-slate-800 px-4 py-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-sm font-semibold tracking-wide text-slate-100"><Database size={17} className="text-emerald-300" /> Context Atlas</div><div className="mt-1 max-w-xl text-[11px] leading-relaxed text-slate-500">A friendly view of the local memory that keeps Ophanim ready to answer.</div><div className="mt-2 flex items-center gap-2 font-mono text-[10px] text-slate-600"><span>{inspect?.database ?? 'context.db'}</span><span>·</span><span>{lastRefresh ? `Updated ${new Date(lastRefresh).toLocaleTimeString('en-US', { hour12: false })}` : 'Syncing…'}</span></div></div><div className="flex items-center gap-2"><div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-black/20 p-1"><button type="button" aria-pressed={mode === 'topology'} onClick={() => setMode('topology')} className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[10px] font-medium transition-colors ${mode === 'topology' ? 'bg-emerald-400/10 text-emerald-100' : 'text-slate-500 hover:text-slate-300'}`}><Eye size={12} /> Overview</button><button type="button" aria-pressed={mode === 'database'} onClick={() => setMode('database')} className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[10px] font-medium transition-colors ${mode === 'database' ? 'bg-cyan-400/10 text-cyan-100' : 'text-slate-500 hover:text-slate-300'}`}><Table2 size={12} /> Database</button></div><button type="button" onClick={() => void refreshInspect()} className="rounded-lg border border-slate-800 p-2 text-slate-500 transition-colors hover:border-cyan-300/30 hover:text-cyan-100" title="Refresh context atlas" aria-label="Refresh context atlas"><RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} /></button></div></div>{contextLedger && <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-violet-400/15 bg-violet-400/[0.04] px-3 py-2 text-[10px] text-violet-200"><span className="font-semibold">Latest model context</span><span className="text-slate-500">~{contextLedger.data.estimated_prompt_tokens ?? '?'} prompt tokens</span><span className="text-slate-500">{contextLedger.data.message_count ?? '?'} messages</span><span className="text-slate-500">Memory-first retrieval enabled</span></div>}</div><div className={`min-h-0 flex-1 p-3 ${mode === 'topology' ? 'overflow-y-auto' : 'overflow-hidden'}`}>{inspect ? (mode === 'topology' ? <Overview inspect={inspect} /> : <DatabaseView inspect={inspect} />) : <div className="flex h-full items-center justify-center text-xs text-slate-600"><CircleDashed size={15} className="mr-2 animate-spin" /> Loading local memory…</div>}</div>{inspect?.warnings.length ? <div className="mx-3 mb-3 flex shrink-0 items-start gap-2 rounded-xl border border-amber-400/20 bg-amber-400/[0.05] px-3 py-2.5 text-[10px] leading-relaxed text-amber-200"><AlertTriangle size={13} className="mt-0.5 shrink-0" /><span>{inspect.warnings.slice(0, 2).join(' · ')}</span></div> : <div className="flex shrink-0 items-center gap-2 border-t border-slate-800 px-4 py-2.5 text-[10px] text-slate-600"><ShieldCheck size={13} className="text-emerald-300" /> READ-ONLY MEMORY VIEW · LIVE ACTIONS STILL REQUIRE VERIFICATION</div>}</section>;
}
