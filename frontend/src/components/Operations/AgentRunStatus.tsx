import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { AlertTriangle, CalendarClock, CheckCircle2, Clock3, RefreshCw, Send, Wrench, XCircle } from 'lucide-react';
import { fetchManagedAgentStatuses, ManagedAgentRunStatus } from '../../lib/api';

function formatTimestamp(value: number | null): string {
  return value ? new Date(value * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '—';
}

function statusTone(status: ManagedAgentRunStatus['overall_status']): string {
  if (status === 'action_failed' || status === 'error') return 'border-rose-400/25 bg-rose-400/[0.06]';
  if (status === 'running') return 'border-cyan-400/25 bg-cyan-400/[0.06]';
  return 'border-emerald-400/20 bg-emerald-400/[0.04]';
}

function statusIcon(status: ManagedAgentRunStatus['overall_status']) {
  if (status === 'action_failed' || status === 'error') return <XCircle size={14} className="text-rose-300" />;
  if (status === 'running') return <Clock3 size={14} className="text-cyan-300" />;
  return <CheckCircle2 size={14} className="text-emerald-300" />;
}

function statusLabel(status: ManagedAgentRunStatus['overall_status']): string {
  return status === 'action_failed' ? 'ACTION FAILED' : status.replace('_', ' ').toUpperCase();
}

function ResultRow({ label, children, icon }: { label: string; children: ReactNode; icon: ReactNode }) {
  return <div className="flex items-start gap-2 rounded-lg border border-slate-800/80 bg-black/10 px-2.5 py-2">
    <span className="mt-0.5 text-slate-500">{icon}</span>
    <div className="min-w-0 flex-1"><div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-slate-600">{label}</div><div className="mt-0.5 truncate text-[11px] text-slate-300">{children}</div></div>
  </div>;
}

export function AgentRunStatus() {
  const [statuses, setStatuses] = useState<ManagedAgentRunStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchManagedAgentStatuses();
      setStatuses(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load agent status.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return <section className="shrink-0 rounded-2xl border border-slate-800 bg-[#080f1b] p-3 shadow-[0_20px_60px_rgba(0,0,0,0.16)] lg:col-span-2">
    <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-3">
      <div><div className="flex items-center gap-2 text-sm font-semibold tracking-wide text-slate-100"><CalendarClock size={17} className="text-cyan-300" /> Agent run status</div><div className="mt-1 text-[11px] text-slate-500">A truthful view of execution, tools, and notification delivery.</div></div>
      <button type="button" onClick={() => void refresh()} className="rounded-lg border border-slate-800 p-2 text-slate-500 transition-colors hover:border-cyan-300/30 hover:text-cyan-100" title="Refresh agent run status" aria-label="Refresh agent run status"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button>
    </div>
    {error ? <div className="flex items-center gap-2 rounded-xl border border-amber-400/20 bg-amber-400/[0.05] px-3 py-2 text-[11px] text-amber-200"><AlertTriangle size={14} /> {error}</div> : statuses.length ? <div className="grid gap-3 md:grid-cols-2">
      {statuses.map((agent) => <article key={agent.id} className={`rounded-xl border p-3 ${statusTone(agent.overall_status)}`}>
        <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="truncate text-sm font-semibold text-slate-100">{agent.name}</div><div className="mt-1 text-[10px] text-slate-500">{agent.schedule_type}{agent.schedule_value ? ` · ${agent.schedule_value}` : ''}</div></div><div className="flex shrink-0 items-center gap-1.5 font-mono text-[9px] tracking-widest text-slate-400">{statusIcon(agent.overall_status)} {statusLabel(agent.overall_status)}</div></div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <ResultRow label="Last run" icon={<Clock3 size={12} />}>{formatTimestamp(agent.last_run_at)} · {agent.last_outcome || 'not run'}</ResultRow>
          <ResultRow label="Next run" icon={<CalendarClock size={12} />}>{formatTimestamp(agent.next_run_at)}</ResultRow>
          <ResultRow label="Tool result" icon={<Wrench size={12} />}>{agent.last_tool_result ? `${agent.last_tool_result.tool} · ${agent.last_tool_result.success === true ? 'succeeded' : agent.last_tool_result.success === false ? 'failed' : 'in progress'}` : 'No tool call recorded'}</ResultRow>
          <ResultRow label="Delivery" icon={<Send size={12} />}>{agent.delivery_result ? `${agent.delivery_result.status} · ${agent.delivery_result.detail}` : 'No notification attempted'}</ResultRow>
        </div>
        {agent.failure_reason ? <div className="mt-2 flex items-start gap-2 rounded-lg border border-rose-400/15 bg-rose-400/[0.04] px-2.5 py-2 text-[11px] leading-relaxed text-rose-200"><AlertTriangle size={13} className="mt-0.5 shrink-0" /><span><span className="font-semibold">Failure reason:</span> {agent.failure_reason}</span></div> : <div className="mt-2 text-[10px] text-slate-600">{agent.current_activity || 'No active operation.'}</div>}
      </article>)}
    </div> : <div className="rounded-xl border border-slate-800/80 px-3 py-5 text-center text-[11px] text-slate-600">{loading ? 'Loading agent status…' : 'No managed agents configured.'}</div>}
  </section>;
}
