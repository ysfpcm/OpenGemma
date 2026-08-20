import { Bot, ShieldCheck } from 'lucide-react';
import { CodexMissions } from '../components/Operations/CodexMissions';
import { ShadowSituations } from '../components/Operations/ShadowSituations';

export function OperationsPage() {
  return <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[#050912] text-slate-100">
    <header className="shrink-0 border-b border-slate-800/80 bg-[#070d17] px-4 py-3 sm:px-6">
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-lg border border-cyan-400/25 bg-cyan-400/[0.06] text-cyan-200"><Bot size={18} /></div><div><div className="font-mono text-sm font-semibold tracking-[0.22em]">CODEX MISSIONS</div><div className="mt-1 text-[11px] text-slate-500">Mission progress, checkpoints, and decisions for Codex work.</div></div></div>
        <span className="flex items-center gap-1.5 rounded border border-slate-800 px-2 py-1.5 font-mono text-[10px] tracking-widest text-slate-500"><ShieldCheck size={12} className="text-emerald-300" /> SAFE VIEW</span>
      </div>
    </header>
    <main className="mx-auto flex min-h-0 w-full max-w-[1800px] flex-1 overflow-y-auto p-3 sm:p-4">
      <div className="space-y-3">
        <ShadowSituations />
        <CodexMissions />
      </div>
    </main>
  </div>;
}
