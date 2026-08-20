import { Activity, Database, Terminal } from 'lucide-react';
import { ContextAtlas } from '../components/Operations/ContextAtlas';
import { LiveTerminal } from '../components/Operations/LiveTerminal';
import { useSystemEvents } from '../lib/useSystemEvents';
import { useAppStore } from '../lib/store';

export function DashboardPage() {
  const { events, connection, clearEvents } = useSystemEvents();
  const apiReachable = useAppStore((state) => state.apiReachable);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[#050912] text-slate-100">
      <header className="shrink-0 border-b border-slate-800/80 bg-[#070d17] px-4 py-3 sm:px-6">
        <div className="mx-auto flex max-w-[1800px] flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-cyan-400/25 bg-cyan-400/[0.06] text-cyan-200"><Terminal size={18} /></div>
            <div><div className="font-mono text-sm font-semibold tracking-[0.22em]">OPENJARVIS DASHBOARD</div><div className="mt-1 text-[11px] text-slate-500">Your live activity feed and local memory workspace.</div></div>
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] tracking-widest text-slate-500">
            <span className="flex items-center gap-1.5 rounded border border-slate-800 px-2 py-1.5"><Activity size={12} className={apiReachable === false ? 'text-rose-300' : 'text-emerald-300'} /> {apiReachable === false ? 'API OFFLINE' : 'API ONLINE'}</span>
            <span className="flex items-center gap-1.5 rounded border border-slate-800 px-2 py-1.5"><Database size={12} className="text-cyan-300" /> LOCAL DATA</span>
          </div>
        </div>
      </header>
      <main className="mx-auto flex min-h-0 w-full max-w-[1800px] flex-1 flex-col gap-3 overflow-y-auto p-3 sm:p-4">
        <div className="min-h-[420px] flex-1"><LiveTerminal events={events} connection={connection} onClear={clearEvents} /></div>
        <div className="min-h-[620px] flex-[1.35]"><ContextAtlas events={events} /></div>
      </main>
    </div>
  );
}
