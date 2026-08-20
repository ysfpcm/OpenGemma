import { AlertTriangle, Bot, CheckCircle2, Clock3, Eye, FileSearch, GitFork, OctagonX, RefreshCw, RotateCcw, Send, TerminalSquare } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { fetchCodexMission, fetchCodexMissions, fetchGuardianTimeline, forkCodexMission, interruptCodexMission, requestCodexCheckpoint, resolveCodexDecision, resumeCodexMission, startCodexMission, steerCodexMission } from '../../lib/api';
import type { CodexMission, GuardianTimeline } from '../../types/operations';

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
  const [direction, setDirection] = useState('');
  const [acting, setActing] = useState(false);
  const [resumeInstruction, setResumeInstruction] = useState('Continue from the prior verified state.');
  const [answer, setAnswer] = useState('');
  const [guardianTimeline, setGuardianTimeline] = useState<GuardianTimeline | null>(null);
  const [objective, setObjective] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [mode, setMode] = useState<'read-only' | 'workspace-write'>('read-only');

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

  const canControl = Boolean(selected?.thread_id && selected?.active_turn_id && ['running', 'observing', 'detached'].includes(selected.status));
  const steer = async () => {
    if (!selected || !direction.trim()) return;
    setActing(true);
    try { setSelected(await steerCodexMission(selected.id, direction)); setDirection(''); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to steer Codex mission.'); }
    finally { setActing(false); }
  };
  const interrupt = async () => {
    if (!selected || !window.confirm('Ask Codex to stop this active turn?')) return;
    setActing(true);
    try { setSelected(await interruptCodexMission(selected.id)); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to interrupt Codex mission.'); }
    finally { setActing(false); }
  };
  const resume = async () => {
    if (!selected || !resumeInstruction.trim()) return;
    setActing(true);
    try { setSelected(await resumeCodexMission(selected.id, resumeInstruction)); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to resume Codex mission.'); }
    finally { setActing(false); }
  };
  const fork = async () => {
    if (!selected) return;
    setActing(true);
    try { setSelected(await forkCodexMission(selected.id)); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to fork Codex mission.'); }
    finally { setActing(false); }
  };
  const checkpoint = async () => {
    if (!selected) return;
    setActing(true);
    try { setSelected(await requestCodexCheckpoint(selected.id)); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to request a checkpoint.'); }
    finally { setActing(false); }
  };
  const decide = async (decisionId: string, value: string) => {
    if (!selected) return;
    setActing(true);
    try { await resolveCodexDecision(selected.id, decisionId, { decision: value }); setSelected(await fetchCodexMission(selected.id)); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to resolve Codex decision.'); }
    finally { setActing(false); }
  };
  const answerInput = async (decisionId: string, questionId: string) => {
    if (!selected || !answer.trim()) return;
    setActing(true);
    try { await resolveCodexDecision(selected.id, decisionId, { answers: { [questionId]: [answer.trim()] } }); setAnswer(''); setSelected(await fetchCodexMission(selected.id)); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to answer Codex.'); }
    finally { setActing(false); }
  };
  const showGuardianChain = async (actionId: string) => {
    try { setGuardianTimeline(await fetchGuardianTimeline(actionId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to load Guardian audit chain.'); }
  };
  const start = async () => {
    if (!objective.trim() || !workspace.trim()) return;
    setActing(true);
    try { const mission = await startCodexMission({ objective: objective.trim(), workspace: workspace.trim(), mode }); setSelected(mission); setObjective(''); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to start Codex mission.'); }
    finally { setActing(false); }
  };
  const guardianActionIds = selected?.controls?.flatMap((control) => {
    const actionId = control.detail.guardian_action_id;
    return typeof actionId === 'string' ? [actionId] : [];
  }) ?? [];

  return <section className="rounded-2xl border border-slate-800 bg-[#080f1b] p-3">
    <div className="flex items-center justify-between px-1 pb-3">
      <div><div className="flex items-center gap-2 text-sm font-semibold"><Bot size={17} className="text-cyan-300" /> Codex missions</div><div className="mt-1 flex items-center gap-1 text-[11px] text-slate-500"><Eye size={12} /> Read-only is the default; workspace-write missions remain root-scoped, budgeted, and network-disabled.</div></div>
      <button type="button" onClick={() => void refresh()} className="rounded-lg border border-slate-800 p-2 text-slate-500" aria-label="Refresh Codex missions"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button>
    </div>
    {error ? <div className="rounded-xl border border-amber-400/20 p-3 text-[11px] text-amber-200"><AlertTriangle size={13} className="mr-2 inline" />{error}</div> : !missions.length ? <div className="rounded-xl border border-slate-800 p-5"><div className="text-sm font-semibold text-slate-300">Start a Codex mission</div><p className="mt-1 text-[11px] text-slate-500">Choose a configured workspace. Read-only is the safe default; workspace-write remains scoped and asks before consequential work.</p><input value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="Mission objective" className="mt-3 w-full rounded-lg border border-slate-700 bg-black/20 px-3 py-2 text-[11px] text-slate-200" /><input value={workspace} onChange={(event) => setWorkspace(event.target.value)} placeholder="Workspace path, for example C:\Projects\Ophanim" className="mt-2 w-full rounded-lg border border-slate-700 bg-black/20 px-3 py-2 text-[11px] text-slate-200" /><div className="mt-2 flex flex-wrap items-center gap-2"><select value={mode} onChange={(event) => setMode(event.target.value as 'read-only' | 'workspace-write')} className="rounded border border-slate-700 bg-black/20 px-2 py-2 text-[10px] text-slate-200"><option value="read-only">Read-only</option><option value="workspace-write">Workspace-write</option></select><button type="button" disabled={!objective.trim() || !workspace.trim() || acting} onClick={() => void start()} className="rounded border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-[10px] text-cyan-100 disabled:opacity-40">Start mission</button></div></div> : <div className="grid gap-3 lg:grid-cols-[320px_1fr]">
      <div className="space-y-2">{missions.map((mission) => <button type="button" key={mission.id} onClick={() => void fetchCodexMission(mission.id).then(setSelected)} className={`w-full rounded-xl border bg-black/10 p-3 text-left ${selected?.id === mission.id ? tone(mission.status) : 'border-slate-800 text-slate-300'}`}><div className="line-clamp-2 text-xs font-semibold">{mission.objective}</div><div className="mt-2 flex items-center justify-between font-mono text-[9px] uppercase tracking-widest"><span>{mission.status}</span><span>{mission.phase}</span></div></button>)}</div>
      {selected && <article className="rounded-xl border border-slate-800 bg-black/10 p-3">
        <div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="text-sm font-semibold">{selected.objective}</h3><p className="mt-1 text-[11px] text-slate-500">{selected.workspace}</p><p className="mt-1 text-[9px] uppercase tracking-widest text-slate-600">Authority: {selected.mode ?? 'read-only'} · network {selected.authority?.network_access === false ? 'disabled' : 'unknown'}</p></div><span className={`rounded border px-2 py-1 font-mono text-[9px] uppercase tracking-widest ${tone(selected.status)}`}>{selected.status}</span></div>
        <div className="mt-3 grid gap-2 sm:grid-cols-3"><div className="rounded-lg border border-slate-800 p-2"><Clock3 size={12} className="mb-1 text-cyan-300" /><div className="text-[9px] uppercase text-slate-600">Progress · {elapsed(selected.created_at)}</div><div className="mt-1 text-[11px]">{selected.progress}</div><div className="mt-1 text-[9px] text-slate-600">Last activity {new Date(selected.last_meaningful_at).toLocaleString()}</div></div><div className="rounded-lg border border-slate-800 p-2"><CheckCircle2 size={12} className="mb-1 text-emerald-300" /><div className="text-[9px] uppercase text-slate-600">Verification</div><div className="mt-1 text-[11px]">{selected.verification_state}</div><div className="mt-1 text-[9px] text-slate-600">Usage {Object.keys(selected.usage).length ? JSON.stringify(selected.usage) : 'not reported'}</div></div><div className="rounded-lg border border-slate-800 p-2"><FileSearch size={12} className="mb-1 text-violet-300" /><div className="text-[9px] uppercase text-slate-600">Observed</div><div className="mt-1 text-[11px]">{selected.commands.length} commands · {selected.tools.length} tools · {selected.files.length} file items</div><div className="mt-1 text-[9px] text-slate-600">{selected.errors.length} errors or blockers</div></div></div>
        <div className="mt-3 rounded-xl border border-cyan-400/15 bg-cyan-400/[0.025] p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><div className="text-[10px] font-semibold uppercase tracking-widest text-cyan-200">Direct the active turn</div><p className="mt-1 text-[10px] text-slate-500">Steer, stop, or ask for a structured checkpoint without changing the mission authority.</p></div><div className="flex gap-2"><button type="button" disabled={!canControl || acting} onClick={() => void checkpoint()} className="inline-flex items-center gap-1.5 rounded-lg border border-violet-400/25 px-2.5 py-1.5 text-[10px] text-violet-100 disabled:cursor-not-allowed disabled:opacity-40">Checkpoint</button><button type="button" disabled={!canControl || acting} onClick={() => void interrupt()} className="inline-flex items-center gap-1.5 rounded-lg border border-rose-400/25 px-2.5 py-1.5 text-[10px] text-rose-200 disabled:cursor-not-allowed disabled:opacity-40"><OctagonX size={12} /> Stop turn</button></div></div><div className="mt-2 flex gap-2"><label className="sr-only" htmlFor={`direction-${selected.id}`}>Direction for Codex</label><input id={`direction-${selected.id}`} value={direction} onChange={(event) => setDirection(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void steer(); } }} disabled={!canControl || acting} placeholder={canControl ? 'Tell Codex what to focus on…' : 'This mission has no controllable active turn'} className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-black/20 px-3 py-2 text-[11px] text-slate-200 outline-none placeholder:text-slate-600 disabled:cursor-not-allowed" /><button type="button" disabled={!canControl || !direction.trim() || acting} onClick={() => void steer()} className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-[10px] text-cyan-100 disabled:cursor-not-allowed disabled:opacity-40"><Send size={12} /> Send</button></div></div>
        {!!selected.budget_state && <div className="mt-3 rounded-xl border border-amber-400/15 bg-amber-400/[0.025] p-3"><div className="text-[9px] uppercase tracking-widest text-amber-200">Budgets</div><div className="mt-1 text-[10px] text-slate-400">{selected.budget_state.exceeded?.length ? `Stopped at: ${selected.budget_state.exceeded.join(', ')}` : 'Within configured limits'} · {JSON.stringify(selected.budget_state.used ?? {})}</div></div>}
        {!!selected.decisions?.length && <div className="mt-3 rounded-xl border border-amber-400/20 bg-amber-400/[0.025] p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-amber-200">Decisions</div><div className="mt-2 space-y-2">{selected.decisions.map((decision) => <div key={decision.id} className="rounded-lg border border-slate-800 p-2"><div className="flex flex-wrap items-center justify-between gap-2 text-[10px]"><span>{decision.kind} · {decision.status}</span><span className="text-slate-500">Codex requested · Guardian {decision.guardian_allows ? 'allows' : 'blocks'} · Marc {decision.marc_approved ? 'approved' : 'not approved'}</span></div><div className="mt-1 text-[10px] text-slate-500">{decision.guardian_reason}</div>{decision.status === 'pending' && <div className="mt-2 flex flex-wrap gap-1.5">{decision.request_method === 'item/tool/requestUserInput' ? <><input value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="Answer the offered question…" className="min-w-[180px] flex-1 rounded border border-slate-700 bg-black/20 px-2 py-1 text-[10px] text-slate-200" /><button type="button" disabled={!answer.trim() || acting || !decision.offered.question_ids?.[0]} onClick={() => void answerInput(decision.id, decision.offered.question_ids?.[0] ?? '')} className="rounded border border-cyan-400/30 px-2 py-1 text-[10px] text-cyan-100 disabled:opacity-40">Answer</button></> : decision.offered.decision_values?.map((value) => <button type="button" key={value} disabled={acting || (value === 'accept' && !decision.guardian_allows)} onClick={() => void decide(decision.id, value)} className="rounded border border-cyan-400/30 px-2 py-1 text-[10px] text-cyan-100 disabled:cursor-not-allowed disabled:opacity-40">{value}</button>)}</div>}</div>)}</div></div>}
        {!!guardianActionIds.length && <div className="mt-3 rounded-xl border border-emerald-400/20 bg-emerald-400/[0.025] p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-emerald-200">Guardian authorization chain</div><p className="mt-1 text-[10px] text-slate-500">Inspect the immutable proposal, authorization, attempt, and verification evidence for this Codex decision.</p><div className="mt-2 flex flex-wrap gap-2">{guardianActionIds.map((actionId) => <button type="button" key={actionId} onClick={() => void showGuardianChain(actionId)} className="rounded border border-emerald-400/30 px-2 py-1 text-[10px] text-emerald-100">View chain</button>)}</div>{guardianTimeline && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-slate-800 p-2 text-[10px] text-slate-400">{JSON.stringify(guardianTimeline, null, 2)}</pre>}</div>}
        <div className="mt-3 grid gap-2 sm:grid-cols-2"><div className="rounded-xl border border-slate-800 bg-black/15 p-3"><div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-slate-300"><RotateCcw size={12} className="text-cyan-300" /> Resume a terminal mission</div><input value={resumeInstruction} onChange={(event) => setResumeInstruction(event.target.value)} disabled={acting} className="mt-2 w-full rounded-lg border border-slate-700 bg-black/20 px-3 py-2 text-[11px] text-slate-200 outline-none disabled:opacity-50" /><button type="button" disabled={!['completed', 'interrupted', 'blocked'].includes(selected.status) || !resumeInstruction.trim() || acting} onClick={() => void resume()} className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-cyan-400/30 px-2.5 py-1.5 text-[10px] text-cyan-100 disabled:cursor-not-allowed disabled:opacity-40">Resume safely</button></div><div className="rounded-xl border border-slate-800 bg-black/15 p-3"><div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-slate-300"><GitFork size={12} className="text-violet-300" /> Explore an alternative</div><p className="mt-2 text-[10px] leading-relaxed text-slate-500">Creates a separate ephemeral Codex thread with the same read-only limits. It cannot modify your workspace.</p><button type="button" disabled={!selected.thread_id || acting} onClick={() => void fork()} className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-violet-400/30 px-2.5 py-1.5 text-[10px] text-violet-100 disabled:cursor-not-allowed disabled:opacity-40">Create fork</button></div></div>
        {!!selected.plan.length && <div className="mt-3"><div className="text-[9px] uppercase tracking-widest text-slate-600">Current plan</div><pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 p-2 text-[10px] text-slate-400">{JSON.stringify(selected.plan, null, 2)}</pre></div>}
        {!!selected.errors.length && <div className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/[0.03] p-2"><div className="text-[9px] uppercase tracking-widest text-amber-300">Errors and blockers</div><pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap text-[10px] text-amber-100/70">{JSON.stringify(selected.errors, null, 2)}</pre></div>}
        <div className="mt-3"><div className="flex items-center gap-1 text-[9px] uppercase tracking-widest text-slate-600"><TerminalSquare size={11} /> Traceable milestones</div><div className="mt-1 max-h-40 space-y-1 overflow-auto">{selected.milestones?.map((item, index) => <div key={`${item.event_fingerprint}-${index}`} className="rounded border border-slate-800 px-2 py-1.5 text-[10px] text-slate-400">{item.summary}</div>)}</div></div>
        {[...selected.commands, ...selected.tools, ...selected.files].length > 0 && <div className="mt-3 text-[10px] text-slate-500">{[...selected.commands, ...selected.tools, ...selected.files].slice(-5).map(itemLabel).join(' · ')}</div>}
      </article>}
    </div>}
  </section>;
}
