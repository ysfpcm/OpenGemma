import { useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import type { ToolInfo } from '../../lib/api';

const CATEGORY_ORDER = [
  'filesystem', 'system', 'code', 'vcs', 'storage', 'memory', 'knowledge',
  'knowledge_graph', 'search', 'network', 'browser', 'database', 'data',
  'math', 'reasoning', 'inference', 'media', 'audio', 'skill', 'communication', 'other',
];

const CATEGORY_LABELS: Record<string, string> = {
  filesystem: 'Files', system: 'Shell & exec', code: 'Code & repl', vcs: 'Git',
  storage: 'Memory & storage', memory: 'Memory', knowledge: 'Knowledge', knowledge_graph: 'Knowledge graph',
  search: 'Search', network: 'Network', browser: 'Browser', database: 'Database', data: 'Data',
  math: 'Math', reasoning: 'Reasoning', inference: 'Inference', media: 'Media', audio: 'Audio',
  skill: 'Skills', communication: 'Messaging', other: 'Other',
};

const TOOL_LABELS: Record<string, string> = {
  commute_readiness: 'Live commute timing',
  traffic_lookup: 'Traffic lookup',
  weather_lookup: 'Weather lookup',
  devotional_content: 'Devotional content',
  channel_send: 'Send a message',
  channel_list: 'Read messages',
  memory_store: 'Remember results',
  memory_retrieve: 'Recall past results',
  web_search: 'Web search',
  http_request: 'Open web pages',
  file_read: 'Read files',
  file_write: 'Write files',
  shell_exec: 'Run shell commands',
  code_interpreter: 'Run code',
  git_status: 'Check Git status',
  git_diff: 'Review Git changes',
  git_log: 'Read Git history',
  knowledge_search: 'Search connected data',
  knowledge_sql: 'Query connected data',
  scan_chunks: 'Scan document chunks',
  think: 'Reason through a task',
};

function displayToolName(name: string): string {
  return TOOL_LABELS[name] || name.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function ToolsPicker({
  tools,
  selected,
  onChange,
}: {
  tools: ToolInfo[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [hovered, setHovered] = useState<ToolInfo | null>(null);
  const [pulseKey, setPulseKey] = useState(0);
  const [query, setQuery] = useState('');

  // Channel destinations are configured in the agent's Messaging tab. The
  // channel_send tool itself remains selectable here when it is registered.
  const selectableTools = useMemo(() => tools.filter((tool) => tool.source !== 'channel'), [tools]);
  const filteredTools = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return selectableTools;
    return selectableTools.filter((tool) => `${tool.name} ${displayToolName(tool.name)} ${tool.description}`.toLowerCase().includes(needle));
  }, [query, selectableTools]);
  const grouped = useMemo(() => {
    const buckets: Record<string, ToolInfo[]> = {};
    for (const tool of filteredTools) {
      const category = CATEGORY_ORDER.includes(tool.category) ? tool.category : 'other';
      (buckets[category] ||= []).push(tool);
    }
    for (const category of Object.keys(buckets)) buckets[category].sort((a, b) => a.name.localeCompare(b.name));
    return CATEGORY_ORDER.filter((category) => buckets[category]?.length).map((category) => ({ category, items: buckets[category] }));
  }, [filteredTools]);

  const configuredNames = selectableTools.filter((tool) => tool.configured).map((tool) => tool.name);
  const allSelected = configuredNames.length > 0 && configuredNames.every((name) => selected.includes(name));
  const selectedCount = selected.filter((name) => selectableTools.some((tool) => tool.name === name)).length;

  const toggle = (name: string) => {
    onChange(selected.includes(name) ? selected.filter((tool) => tool !== name) : [...selected, name]);
    setPulseKey((key) => key + 1);
  };

  const hint = hovered
    ? hovered.configured
      ? hovered.description || hovered.name
      : `Needs ${hovered.credential_keys.join(', ') || 'service setup'}`
    : 'Select a capability to see what it does';

  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label className="block text-[13px] font-medium" style={{ color: 'var(--color-text-secondary)' }}>Tools this agent can use</label>
        <div className="flex items-center gap-2">
          <span key={pulseKey} className="tools-count" style={{ fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace', fontSize: 10.5, color: 'var(--color-text-tertiary)' }}>
            <span style={{ color: 'var(--color-accent)' }}>{selectedCount}</span><span style={{ opacity: 0.5 }}> selected</span>
          </span>
          <span style={{ color: 'var(--color-text-tertiary)', opacity: 0.3 }}>·</span>
          <button type="button" onClick={() => onChange(allSelected ? [] : configuredNames)} disabled={tools.length === 0} className="transition-colors" style={{ fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace', fontSize: 10, color: 'var(--color-text-tertiary)', background: 'none', border: 'none', padding: 0, cursor: tools.length === 0 ? 'default' : 'pointer', textDecoration: 'underline', textUnderlineOffset: 2 }}>
            {allSelected ? 'clear' : 'select all'}
          </button>
        </div>
      </div>
      <p className="text-[10.5px] mb-2" style={{ color: 'var(--color-text-tertiary)' }}>Templates preselect a sensible tool set. Leave it as-is unless you want to customize the workflow.</p>
      {tools.length === 0 ? (
        <div className="px-3 py-2 rounded-lg text-xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text-tertiary)' }}>Loading available capabilities...</div>
      ) : (
        <div className="rounded-lg overflow-hidden" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }} onMouseLeave={() => setHovered(null)}>
          <div className="px-2.5 pt-2.5">
            <div className="relative mb-2">
              <Search size={13} className="absolute left-2.5 top-2.5" style={{ color: 'var(--color-text-tertiary)' }} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find a capability" className="w-full pl-8 pr-3 py-1.5 rounded-md text-xs bg-transparent" style={{ border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
            </div>
          </div>
          <div className="px-2.5 pb-2.5 overflow-y-auto" style={{ maxHeight: 210 }}>
            {grouped.map(({ category, items }, index) => (
              <div key={category} style={{ marginTop: index === 0 ? 0 : 10 }}>
                <div className="flex items-center gap-1.5 mb-1.5" style={{ fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace', fontSize: 9.5, color: 'var(--color-text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  <span style={{ opacity: 0.5 }}>-</span><span>{CATEGORY_LABELS[category] || category}</span><span className="flex-1" style={{ borderBottom: '1px dashed var(--color-border)', marginBottom: 3, opacity: 0.5 }} />
                </div>
                <div className="flex flex-wrap gap-1">
                  {items.map((tool) => {
                    const isSelected = selected.includes(tool.name);
                    const disabled = !tool.configured;
                    return (
                      <button key={tool.name} type="button" disabled={disabled} onClick={() => toggle(tool.name)} onMouseEnter={() => setHovered(tool)} onFocus={() => setHovered(tool)} title={disabled ? `Needs ${tool.credential_keys.join(', ') || 'service setup'}` : tool.description} className="tool-chip" style={{ fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace', fontSize: 11, lineHeight: 1.2, padding: '4px 7px 4px 5px', borderRadius: 4, display: 'inline-flex', alignItems: 'center', gap: 5, background: isSelected ? 'color-mix(in srgb, var(--color-accent) 14%, transparent)' : 'var(--color-bg)', color: disabled ? 'var(--color-text-tertiary)' : isSelected ? 'var(--color-accent)' : 'var(--color-text-secondary)', border: disabled ? '1px dashed var(--color-border)' : `1px solid ${isSelected ? 'var(--color-accent)' : 'var(--color-border)'}`, boxShadow: isSelected ? 'inset 0 0 0 1px color-mix(in srgb, var(--color-accent) 30%, transparent)' : 'none', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.55 : 1, transition: 'background 120ms, color 120ms, border-color 120ms, transform 80ms' }}>
                        <span style={{ opacity: isSelected ? 1 : 0.5, color: disabled ? 'var(--color-text-tertiary)' : isSelected ? 'var(--color-accent)' : 'var(--color-text-tertiary)', fontSize: 10.5 }}>{disabled ? 'x' : isSelected ? '✓' : '+'}</span>
                        <span>{displayToolName(tool.name)}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
            {grouped.length === 0 && <div className="py-4 text-center text-xs" style={{ color: 'var(--color-text-tertiary)' }}>No capabilities match that search.</div>}
          </div>
          <div className="flex items-center gap-2 px-2.5 py-1.5" style={{ borderTop: '1px solid var(--color-border)', background: 'var(--color-bg)', fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace', fontSize: 10.5, color: 'var(--color-text-tertiary)', minHeight: 28 }}>
            <span style={{ color: hovered ? (hovered.configured ? 'var(--color-accent)' : '#f59e0b') : 'var(--color-text-tertiary)', opacity: hovered ? 1 : 0.5 }}>{hovered ? (hovered.configured ? '>' : '!') : '-'}</span>
            {hovered && <span style={{ color: 'var(--color-text)', fontWeight: 500 }}>{displayToolName(hovered.name)}</span>}
            <span className="truncate" style={{ flex: 1, color: 'var(--color-text-tertiary)' }}>{hovered ? `- ${hint}` : hint}</span>
          </div>
        </div>
      )}
      <style>{`@keyframes tools-count-pulse { 0% { transform: scale(1); } 40% { transform: scale(1.18); } 100% { transform: scale(1); } } .tools-count { display: inline-block; animation: tools-count-pulse 220ms ease-out; }`}</style>
    </div>
  );
}
