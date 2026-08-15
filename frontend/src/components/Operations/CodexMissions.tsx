import { AlertTriangle, Bot, CheckCircle2, Clock3, Eye, FileSearch, RefreshCw, TerminalSquare } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { fetchCodexMission, fetchCodexMissions } from '../../lib/api';
import type { CodexMission } from '../../types/operations';

function tone(status: string): string {
  if (['failed', 'blocked', 'interrupted'].includes(status)) return 'border-amber-400/25 text-amber-200';
  if (status === 'completed') return 'border-emerald-400/25 text-emerald-200';
  return 'border-cyan-400/25 text-cyan-200';
}

function itemLabel(item: Record<string, unknown>): string {
  return String(item.command ?? item.name ?? item.path ?? item.type ?? 'Observed item');
}

function elapsed(createdAt: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(createdAt)) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export function CodexMissions() {
  const [missions, setMissions] = useState<CodexMission[]>([]);
  const [selected, setSelected] = useState<CodexMission | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchCodexMissions();
      setMissions(next);
      if (selected) setSelected(await fetchCodexMission(selected.id));
      else if (next[0]) setSelected(await fetchCodexMission(next[0].id));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load Codex missions.');
    } finally { setLoading(false); }
  }, [selected?.id]);

  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 10000); return () => window.clearInterval(timer); }, [refresh]);

  return <section className="rounded-2xl border border-slate-800 bg-[#080f1b] p-3 lg:col-span-2">
    <div className="flex items-center justify-between px-1 pb-3">
      <div><div className="flex items-center gap-2 text-sm font-semibold"><Bot size={17} className="text-cyan-300" /> Codex missions</div><div className="mt-1 flex items-center gap-1 text-[11px] text-slate-500"><Eye size={12} /> Read-only observation. No steering or approval controls.</div></div>
      <button type="button" onClick={() => void refresh()} className="rounded-lg border border-slate-800 p-2 text-slate-500" aria-label="Refresh Codex missions"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button>
    </div>
    {error ? <div className="rounded-xl border border-amber-400/20 p-3 text-[11px] text-amber-200"><AlertTriangle size={13} className="mr-2 inline" />{error}</div> : !missions.length ? <div className="rounded-xl border border-slate-800 p-5 text-center text-[11px] text-slate-600">No observed Codex missions yet.</div> : <div className="grid gap-3 lg:grid-cols-[320px_1fr]">
      <div className="space-y-2">{missions.map((mission) => <button type="button" key={mission.id} onClick={() => void fetchCodexMission(mission.id).then(setSelected)} className={`w-full rounded-xl border bg-black/10 p-3 text-left ${selected?.id === mission.id ? tone(mission.status) : 'border-slate-800 text-slate-300'}`}><div className="line-clamp-2 text-xs font-semibold">{mission.objective}</div><div className="mt-2 flex items-center justify-between font-mono text-[9px] uppercase tracking-widest"><span>{mission.status}</span><span>{mission.phase}</span></div></button>)}</div>
      {selected && <article className="rounded-xl border border-slate-800 bg-black/10 p-3">
        <div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="text-sm font-semibold">{selected.objective}</h3><p className="mt-1 text-[11px] text-slate-500">{selected.workspace}</p></div><span className={`rounded border px-2 py-1 font-mono text-[9px] uppercase tracking-widest ${tone(selected.status)}`}>{selected.status}</span></div>
        <div className="mt-3 grid gap-2 sm:grid-cols-3"><div className="rounded-lg border border-slate-800 p-2"><Clock3 size={12} className="mb-1 text-cyan-300" /><div className="text-[9px] uppercase text-slate-600">Progress · {elapsed(selected.created_at)}</div><div className="mt-1 text-[11px]">{selected.progress}</div><div className="mt-1 text-[9px] text-slate-600">Last activity {new Date(selected.last_meaningful_at).toLocaleString()}</div></div><div className="rounded-lg border border-slate-800 p-2"><CheckCircle2 size={12} className="mb-1 text-emerald-300" /><div className="text-[9px] uppercase text-slate-600">Verification</div><div className="mt-1 text-[11px]">{selected.verification_state}</div><div className="mt-1 text-[9px] text-slate-600">Usage {Object.keys(selected.usage).length ? JSON.stringify(selected.usage) : 'not reported'}</div></div><div className="rounded-lg border border-slate-800 p-2"><FileSearch size={12} className="mb-1 text-violet-300" /><div className="text-[9px] uppercase text-slate-600">Observed</div><div className="mt-1 text-[11px]">{selected.commands.length} commands · {selected.tools.length} tools · {selected.files.length} file items</div><div className="mt-1 text-[9px] text-slate-600">{selected.errors.length} errors or blockers</div></div></div>
        {!!selected.plan.length && <div className="mt-3"><div className="text-[9px] uppercase tracking-widest text-slate-600">Current plan</div><pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 p-2 text-[10px] text-slate-400">{JSON.stringify(selected.plan, null, 2)}</pre></div>}
        {!!selected.errors.length && <div className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/[0.03] p-2"><div className="text-[9px] uppercase tracking-widest text-amber-300">Errors and blockers</div><pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap text-[10px] text-amber-100/70">{JSON.stringify(selected.errors, null, 2)}</pre></div>}
        <div className="mt-3"><div className="flex items-center gap-1 text-[9px] uppercase tracking-widest text-slate-600"><TerminalSquare size={11} /> Traceable milestones</div><div className="mt-1 max-h-40 space-y-1 overflow-auto">{selected.milestones?.map((item, index) => <div key={`${item.event_fingerprint}-${index}`} className="rounded border border-slate-800 px-2 py-1.5 text-[10px] text-slate-400">{item.summary}</div>)}</div></div>
        {[...selected.commands, ...selected.tools, ...selected.files].length > 0 && <div className="mt-3 text-[10px] text-slate-500">{[...selected.commands, ...selected.tools, ...selected.files].slice(-5).map(itemLabel).join(' · ')}</div>}
      </article>}
    </div>}
  </section>;
}
