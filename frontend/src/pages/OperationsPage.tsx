import { Activity, Cpu, ShieldCheck, Terminal } from 'lucide-react';
import { AgentRunStatus } from '../components/Operations/AgentRunStatus';
import { ContextAtlas } from '../components/Operations/ContextAtlas';
import { CodexMissions } from '../components/Operations/CodexMissions';
import { LiveTerminal } from '../components/Operations/LiveTerminal';
import { useSystemEvents } from '../lib/useSystemEvents';
import { useAppStore } from '../lib/store';

export function OperationsPage() {
  const { events, connection, clearEvents } = useSystemEvents();
  const serverInfo = useAppStore((state) => state.serverInfo);
  const apiReachable = useAppStore((state) => state.apiReachable);

  return <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[#050912] text-slate-100">
    <header className="shrink-0 border-b border-slate-800/80 bg-[#070d17] px-4 py-3 sm:px-6">
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-lg border border-cyan-400/25 bg-cyan-400/[0.06] text-cyan-200"><Terminal size={18} /></div><div><div className="font-mono text-sm font-semibold tracking-[0.22em]">OPENJARVIS OPERATIONS</div><div className="mt-1 text-[11px] text-slate-500">Runtime visibility for the systems behind your phone conversations.</div></div></div>
        <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] tracking-widest text-slate-500"><span className="flex items-center gap-1.5 rounded border border-slate-800 px-2 py-1.5"><Activity size={12} className={apiReachable === false ? 'text-rose-300' : 'text-emerald-300'} /> {apiReachable === false ? 'API OFFLINE' : 'API ONLINE'}</span><span className="flex items-center gap-1.5 rounded border border-slate-800 px-2 py-1.5"><Cpu size={12} className="text-cyan-300" /> {serverInfo?.model || 'MODEL UNKNOWN'}</span><span className="flex items-center gap-1.5 rounded border border-slate-800 px-2 py-1.5"><ShieldCheck size={12} className="text-emerald-300" /> SAFE VIEW</span></div>
      </div>
    </header>
    <main className="mx-auto flex min-h-0 w-full max-w-[1800px] flex-1 flex-col gap-3 overflow-y-auto p-3 sm:p-4 lg:grid lg:grid-cols-2">
      <AgentRunStatus />
      <CodexMissions />
      <LiveTerminal events={events} connection={connection} onClear={clearEvents} />
      <ContextAtlas events={events} />
    </main>
  </div>;
}
