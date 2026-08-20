import { AlertTriangle, Eye, RefreshCw, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { fetchShadowSituations } from '../../lib/api';
import type { ShadowSituation, ShadowSituationView } from '../../types/operations';

function statusTone(status: string): string {
  if (status === 'closed') return 'border-slate-700 text-slate-400';
  if (status === 'uncertain') return 'border-amber-400/25 text-amber-200';
  return 'border-cyan-400/25 text-cyan-200';
}

function renderSituation(situation: ShadowSituation) {
  return <article key={situation.id} className="rounded-xl border border-slate-800 bg-black/10 p-3">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <div className="text-sm font-semibold text-slate-200">{situation.situation_type}</div>
        <div className="mt-1 font-mono text-[9px] uppercase tracking-widest text-slate-600">{situation.id}</div>
      </div>
      <span className={`rounded border px-2 py-1 font-mono text-[9px] uppercase tracking-widest ${statusTone(situation.status)}`}>{situation.status}</span>
    </div>
    <div className="mt-3 grid gap-2 sm:grid-cols-3">
      <div className="rounded-lg border border-slate-800 p-2"><div className="text-[9px] uppercase tracking-widest text-slate-600">Confidence</div><div className="mt-1 text-xs text-slate-200">{situation.confidence == null ? 'unknown' : `${Math.round(situation.confidence * 100)}%`}</div></div>
      <div className="rounded-lg border border-slate-800 p-2"><div className="text-[9px] uppercase tracking-widest text-slate-600">Evidence</div><div className="mt-1 text-xs text-slate-200">{situation.evidence_ids.length} cited IDs</div></div>
      <div className="rounded-lg border border-slate-800 p-2"><div className="text-[9px] uppercase tracking-widest text-slate-600">Uncertainty</div><div className="mt-1 text-xs text-slate-200">{situation.uncertainty.length ? situation.uncertainty.join(', ') : 'none recorded'}</div></div>
    </div>
  </article>;
}

export function ShadowSituations() {
  const [view, setView] = useState<ShadowSituationView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setView(await fetchShadowSituations());
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load shadow situations.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return <section className="rounded-2xl border border-cyan-400/15 bg-[#080f1b] p-3">
    <div className="flex items-center justify-between px-1 pb-3">
      <div><div className="flex items-center gap-2 text-sm font-semibold"><Eye size={17} className="text-cyan-300" /> Shadow situations</div><div className="mt-1 flex items-center gap-1 text-[11px] text-slate-500"><ShieldCheck size={12} /> Evidence-backed observation only; no notifications, approvals, or actions.</div></div>
      <button type="button" onClick={() => void refresh()} className="rounded-lg border border-slate-800 p-2 text-slate-500" aria-label="Refresh shadow situations"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button>
    </div>
    {error ? <div className="rounded-xl border border-amber-400/20 p-3 text-[11px] text-amber-200"><AlertTriangle size={13} className="mr-2 inline" />{error}</div> : !view?.situations.length ? <div className="rounded-xl border border-slate-800 p-4 text-[11px] text-slate-500">No shadow situations currently recorded. Blocked evaluations remain available from the read-only Phase 4 evidence API.</div> : <div className="space-y-2">{view.situations.map(renderSituation)}</div>}
  </section>;
}
