import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Clipboard, History, Pause, Play, Search, Trash2, X } from 'lucide-react';
import type { SystemEvent, SystemEventCategory, SystemEventConnection } from '../../types/operations';

const CATEGORY_OPTIONS: Array<{ id: SystemEventCategory | 'all'; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'inference', label: 'Inference' },
  { id: 'tools', label: 'Tools' },
  { id: 'context', label: 'Context' },
  { id: 'cameras', label: 'Cameras' },
  { id: 'channels', label: 'Channels' },
  { id: 'agents', label: 'Agents' },
  { id: 'security', label: 'Security' },
];

const LEVEL_COLOR: Record<SystemEvent['level'], string> = {
  info: '#7dd3fc',
  warn: '#fbbf24',
  error: '#fb7185',
};

const CATEGORY_COLOR: Record<SystemEventCategory, string> = {
  inference: '#67e8f9',
  tools: '#c4b5fd',
  context: '#86efac',
  cameras: '#f9a8d4',
  channels: '#f0abfc',
  traces: '#fde68a',
  agents: '#93c5fd',
  security: '#fda4af',
  system: '#94a3b8',
};

function eventDate(timestamp: number): Date {
  return new Date(timestamp < 1_000_000_000_000 ? timestamp * 1000 : timestamp);
}

function formatTime(timestamp: number): string {
  return eventDate(timestamp).toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function connectionLabel(connection: SystemEventConnection): string {
  if (connection === 'connected') return 'LIVE STREAM';
  if (connection === 'connecting') return 'CONNECTING';
  return 'RECONNECTING';
}

function ConnectionDot({ connection }: { connection: SystemEventConnection }) {
  const color = connection === 'connected' ? '#4ade80' : connection === 'connecting' ? '#fbbf24' : '#fb7185';
  return <span className="inline-block h-2 w-2 rounded-full" style={{ background: color, boxShadow: `0 0 9px ${color}` }} />;
}

function TerminalRow({ event }: { event: SystemEvent }) {
  const [expanded, setExpanded] = useState(false);
  const hasData = Object.keys(event.data).length > 0;
  return (
    <div
      className="border-b border-slate-800/60 px-3 py-2 transition-colors hover:bg-slate-900/70"
      style={{ contentVisibility: 'auto' }}
    >
      <button
        type="button"
        onClick={() => hasData && setExpanded((value) => !value)}
        className="grid w-full grid-cols-[68px_68px_86px_1fr_20px] items-start gap-2 text-left font-mono text-[11px] leading-5"
        aria-expanded={expanded}
        aria-label={hasData ? `${expanded ? 'Hide' : 'Show'} event metadata` : undefined}
      >
        <span className="text-slate-500">{formatTime(event.timestamp)}</span>
        <span className="uppercase tracking-wider" style={{ color: LEVEL_COLOR[event.level] }}>
          [{event.level}]
        </span>
        <span className="truncate uppercase tracking-wider" style={{ color: CATEGORY_COLOR[event.category] }}>
          {event.category}
        </span>
        <span className="min-w-0 break-words text-slate-200">{event.summary}</span>
        <span className="text-slate-500">
          {hasData ? (expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />) : null}
        </span>
      </button>
      {expanded && (
        <pre className="mt-2 overflow-x-auto rounded border border-slate-800 bg-black/30 p-2 text-[10px] leading-5 text-slate-400">
          {JSON.stringify(event.data, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function LiveTerminal({
  events,
  connection,
  onClear,
}: {
  events: SystemEvent[];
  connection: SystemEventConnection;
  onClear: () => void;
}) {
  const [category, setCategory] = useState<SystemEventCategory | 'all'>('all');
  const [query, setQuery] = useState('');
  const [follow, setFollow] = useState(true);
  const [showHistory, setShowHistory] = useState(false);
  const terminalRef = useRef<HTMLDivElement>(null);

  const filteredEvents = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return events.filter((event) => {
      if (category !== 'all' && event.category !== category) return false;
      if (!normalizedQuery) return true;
      return `${event.type} ${event.category} ${event.summary}`.toLowerCase().includes(normalizedQuery);
    });
  }, [category, events, query]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!follow || !terminal) return;

    // Scroll only the terminal's own viewport. scrollIntoView() also walks
    // ancestor scroll containers, which can unexpectedly move the whole
    // Operations page whenever a live event arrives.
    const frame = requestAnimationFrame(() => {
      terminal.scrollTo({ top: terminal.scrollHeight, behavior: 'smooth' });
    });
    return () => cancelAnimationFrame(frame);
  }, [filteredEvents.length, filteredEvents[filteredEvents.length - 1]?.timestamp, follow]);

  const copyVisible = async () => {
    const text = filteredEvents
      .map((event) => `${formatTime(event.timestamp)} [${event.level}] [${event.category}] ${event.summary}`)
      .join('\n');
    await navigator.clipboard?.writeText(text);
  };

  return (
    <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-800 bg-[#070d17] shadow-[0_20px_60px_rgba(0,0,0,0.22)]">
      <div className="shrink-0 border-b border-slate-800 px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 font-mono">
            <span className="text-cyan-300">▣</span>
            <h2 className="text-xs font-semibold tracking-[0.24em] text-slate-100">LIVE TERMINAL</h2>
            <span className="flex items-center gap-1.5 text-[10px] tracking-widest text-slate-500">
              <ConnectionDot connection={connection} /> {connectionLabel(connection)}
            </span>
          </div>
          <div className="flex items-center gap-2 text-[10px] font-mono text-slate-500">
            <span>{filteredEvents.length}/{events.length} events</span>
            <button type="button" onClick={() => setFollow((value) => !value)} className="flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-slate-300 hover:border-cyan-400/60 hover:text-cyan-200">
              {follow ? <Pause size={11} /> : <Play size={11} />} {follow ? 'FOLLOW' : 'PAUSED'}
            </button>
            <button type="button" onClick={() => setShowHistory(true)} className="flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-slate-300 hover:border-cyan-400/60 hover:text-cyan-200" title="Show event history">
              <History size={11} /> HISTORY
            </button>
            <button type="button" onClick={copyVisible} className="rounded border border-slate-700 p-1.5 text-slate-300 hover:border-cyan-400/60 hover:text-cyan-200" title="Copy visible events">
              <Clipboard size={12} />
            </button>
            <button type="button" onClick={onClear} className="rounded border border-slate-700 p-1.5 text-slate-300 hover:border-rose-400/60 hover:text-rose-200" title="Clear terminal">
              <Trash2 size={12} />
            </button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <div className="flex min-w-[180px] flex-1 items-center gap-2 rounded border border-slate-800 bg-black/20 px-2 py-1.5">
            <Search size={13} className="text-slate-600" />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter events..." className="w-full bg-transparent font-mono text-[11px] text-slate-200 outline-none placeholder:text-slate-600" />
          </div>
          <div className="flex flex-wrap gap-1">
            {CATEGORY_OPTIONS.map((option) => (
              <button
                type="button"
                key={option.id}
                onClick={() => setCategory(option.id)}
                className="rounded border px-2 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors"
                style={{
                  borderColor: category === option.id ? 'rgba(103,232,249,0.65)' : 'rgb(30 41 59)',
                  color: category === option.id ? '#a5f3fc' : '#64748b',
                  background: category === option.id ? 'rgba(8,145,178,0.12)' : 'transparent',
                }}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div ref={terminalRef} className="min-h-0 flex-1 overflow-y-auto font-mono">
        {filteredEvents.length === 0 ? (
          <div className="flex h-full min-h-56 items-center justify-center px-6 text-center text-xs text-slate-600">
            {connection === 'connected' ? 'Waiting for relevant OpenJarvis activity...' : 'The event stream is not connected yet.'}
          </div>
        ) : (
          filteredEvents.map((event, index) => <TerminalRow key={`${event.timestamp}-${event.type}-${index}`} event={event} />)
        )}
      </div>
      <div className="flex shrink-0 items-center justify-between border-t border-slate-800 px-4 py-2 font-mono text-[10px] tracking-widest text-slate-600">
        <span>SAFE OPERATOR FEED · CONTENT REDACTED</span>
        <span>{events.length >= 2000 ? 'BUFFER FULL' : 'BUFFER 2000'}</span>
      </div>
      {showHistory && (
        <div className="absolute inset-0 z-20 flex min-h-0 flex-col bg-[#070d17]">
          <div className="flex shrink-0 items-center justify-between border-b border-slate-800 px-4 py-3">
            <div>
              <div className="flex items-center gap-2 font-mono text-xs font-semibold tracking-[0.2em] text-slate-100"><History size={14} className="text-cyan-300" /> EVENT HISTORY</div>
              <div className="mt-1 font-mono text-[10px] tracking-widest text-slate-600">SERVER REPLAY Â· {events.length} LOADED</div>
            </div>
            <div className="flex items-center gap-2">
              <button type="button" onClick={copyVisible} className="rounded border border-slate-700 p-1.5 text-slate-300 hover:border-cyan-400/60 hover:text-cyan-200" title="Copy visible history"><Clipboard size={12} /></button>
              <button type="button" onClick={() => setShowHistory(false)} className="rounded border border-slate-700 p-1.5 text-slate-300 hover:border-cyan-400/60 hover:text-cyan-200" title="Close event history"><X size={13} /></button>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto font-mono">
            {filteredEvents.length === 0 ? (
              <div className="flex h-full items-center justify-center px-6 text-center text-xs text-slate-600">No matching events in the server history.</div>
            ) : (
              filteredEvents.map((event, index) => <TerminalRow key={`${event.id ?? event.timestamp}-${event.type}-${index}`} event={event} />)
            )}
          </div>
        </div>
      )}
    </section>
  );
}
