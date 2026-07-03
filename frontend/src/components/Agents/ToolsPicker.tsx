import { useState } from 'react';
import type { ToolInfo } from '../../lib/api';

const TOOL_CATEGORY_ORDER = [
  'filesystem', 'system', 'code', 'vcs', 'storage', 'memory', 'knowledge',
  'knowledge_graph', 'search', 'network', 'browser', 'database', 'data',
  'math', 'reasoning', 'inference', 'media', 'audio', 'skill', 'channel',
  'communication', 'other',
];

const TOOL_CATEGORY_LABELS: Record<string, string> = {
  filesystem: 'filesystem', system: 'shell & exec', code: 'code & repl', vcs: 'git',
  storage: 'memory · storage', memory: 'memory', knowledge: 'knowledge', knowledge_graph: 'knowledge graph',
  search: 'search', network: 'network', browser: 'browser', database: 'database', data: 'data',
  math: 'math', reasoning: 'reasoning', inference: 'inference', media: 'media', audio: 'audio',
  skill: 'skills', channel: 'channel primitives', communication: 'channels', other: 'other',
};

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

  // Channels (source === 'channel') live in ChannelRegistry and aren't
  // directly callable by the LLM — the agent talks to them through the
  // `channel_send` tool. Showing them in the tools picker is misleading,
  // so filter them out; channel bindings are configured separately.
  const tollableTools = tools.filter((t) => t.source !== 'channel');

  // Group by category, respecting the preferred order then alphabetical.
  const grouped = (() => {
    const buckets: Record<string, ToolInfo[]> = {};
    for (const t of tollableTools) {
      const cat = TOOL_CATEGORY_ORDER.includes(t.category) ? t.category : 'other';
      (buckets[cat] ||= []).push(t);
    }
    for (const cat of Object.keys(buckets)) {
      buckets[cat].sort((a, b) => a.name.localeCompare(b.name));
    }
    return TOOL_CATEGORY_ORDER
      .filter((cat) => buckets[cat]?.length)
      .map((cat) => ({ category: cat, items: buckets[cat] }));
  })();

  const configurable = tollableTools.filter((t) => t.configured).map((t) => t.name);
  const allSelected =
    configurable.length > 0 && configurable.every((n) => selected.includes(n));

  const toggle = (name: string) => {
    const next = selected.includes(name)
      ? selected.filter((t) => t !== name)
      : [...selected, name];
    onChange(next);
    setPulseKey((k) => k + 1);
  };

  const hint = hovered
    ? hovered.configured
      ? hovered.description || hovered.name
      : `Needs ${hovered.credential_keys.join(', ') || 'credentials'}`
    : 'hover a tool for details';

  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label
          className="block text-[13px] font-medium"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          Tools
        </label>
        <div className="flex items-center gap-2">
          <span
            key={pulseKey}
            className="tools-count"
            style={{
              fontFamily:
                'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
              fontSize: 10.5,
              color: 'var(--color-text-tertiary)',
            }}
          >
            <span style={{ color: 'var(--color-accent)' }}>
              {selected.length}
            </span>
            <span style={{ opacity: 0.5 }}> / {tollableTools.length}</span>
          </span>
          <span style={{ color: 'var(--color-text-tertiary)', opacity: 0.3 }}>·</span>
          <button
            type="button"
            onClick={() => onChange(allSelected ? [] : configurable)}
            disabled={tools.length === 0}
            className="transition-colors"
            style={{
              fontFamily:
                'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
              fontSize: 10,
              color: 'var(--color-text-tertiary)',
              background: 'none',
              border: 'none',
              padding: 0,
              cursor: tools.length === 0 ? 'default' : 'pointer',
              textDecoration: 'underline',
              textUnderlineOffset: 2,
            }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.color = 'var(--color-text)')
            }
            onMouseLeave={(e) =>
              (e.currentTarget.style.color = 'var(--color-text-tertiary)')
            }
          >
            {allSelected ? 'none' : 'all'}
          </button>
        </div>
      </div>
      <p
        className="text-[10.5px] mb-2"
        style={{ color: 'var(--color-text-tertiary)' }}
      >
        What the agent is allowed to call. An empty selection makes a
        chat-only agent.
      </p>
      {tools.length === 0 ? (
        <div
          className="px-3 py-2 rounded-lg text-xs"
          style={{
            background: 'var(--color-bg-secondary)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text-tertiary)',
          }}
        >
          Loading available tools…
        </div>
      ) : (
        <div
          className="rounded-lg overflow-hidden"
          style={{
            background: 'var(--color-bg-secondary)',
            border: '1px solid var(--color-border)',
          }}
          onMouseLeave={() => setHovered(null)}
        >
          <div
            className="px-2.5 py-2 overflow-y-auto"
            style={{ maxHeight: 200 }}
          >
            {grouped.map(({ category, items }, idx) => (
              <div key={category} style={{ marginTop: idx === 0 ? 0 : 10 }}>
                <div
                  className="flex items-center gap-1.5 mb-1.5"
                  style={{
                    fontFamily:
                      'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
                    fontSize: 9.5,
                    color: 'var(--color-text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.1em',
                  }}
                >
                  <span style={{ opacity: 0.5 }}>─</span>
                  <span>{TOOL_CATEGORY_LABELS[category] || category}</span>
                  <span
                    className="flex-1"
                    style={{
                      borderBottom: '1px dashed var(--color-border)',
                      marginBottom: 3,
                      opacity: 0.5,
                    }}
                  />
                </div>
                <div className="flex flex-wrap gap-1">
                  {items.map((tool) => {
                    const isSelected = selected.includes(tool.name);
                    const disabled = !tool.configured;
                    return (
                      <button
                        key={tool.name}
                        type="button"
                        disabled={disabled}
                        onClick={() => toggle(tool.name)}
                        onMouseEnter={() => setHovered(tool)}
                        onFocus={() => setHovered(tool)}
                        className="tool-chip"
                        style={{
                          fontFamily:
                            'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
                          fontSize: 11,
                          lineHeight: 1.2,
                          padding: '3px 7px 3px 5px',
                          borderRadius: 4,
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 5,
                          background: isSelected
                            ? 'color-mix(in srgb, var(--color-accent) 14%, transparent)'
                            : 'var(--color-bg)',
                          color: disabled
                            ? 'var(--color-text-tertiary)'
                            : isSelected
                              ? 'var(--color-accent)'
                              : 'var(--color-text-secondary)',
                          border: disabled
                            ? '1px dashed var(--color-border)'
                            : `1px solid ${isSelected ? 'var(--color-accent)' : 'var(--color-border)'}`,
                          boxShadow: isSelected
                            ? 'inset 0 0 0 1px color-mix(in srgb, var(--color-accent) 30%, transparent)'
                            : 'none',
                          cursor: disabled ? 'not-allowed' : 'pointer',
                          opacity: disabled ? 0.55 : 1,
                          transition:
                            'background 120ms, color 120ms, border-color 120ms, transform 80ms',
                        }}
                        onMouseDown={(e) =>
                          !disabled && (e.currentTarget.style.transform = 'scale(0.97)')
                        }
                        onMouseUp={(e) =>
                          (e.currentTarget.style.transform = 'scale(1)')
                        }
                      >
                        <span
                          style={{
                            opacity: isSelected ? 1 : 0.5,
                            color: disabled
                              ? 'var(--color-text-tertiary)'
                              : isSelected
                                ? 'var(--color-accent)'
                                : 'var(--color-text-tertiary)',
                            fontSize: 10.5,
                          }}
                        >
                          {disabled ? '⨯' : isSelected ? '▣' : '□'}
                        </span>
                        <span>{tool.name}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
          {/* Live description strip */}
          <div
            className="flex items-center gap-2 px-2.5 py-1.5"
            style={{
              borderTop: '1px solid var(--color-border)',
              background: 'var(--color-bg)',
              fontFamily:
                'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
              fontSize: 10.5,
              color: 'var(--color-text-tertiary)',
              minHeight: 26,
            }}
          >
            <span
              style={{
                color: hovered
                  ? hovered.configured
                    ? 'var(--color-accent)'
                    : '#f59e0b'
                  : 'var(--color-text-tertiary)',
                opacity: hovered ? 1 : 0.5,
              }}
            >
              {hovered ? (hovered.configured ? '▸' : '!') : '·'}
            </span>
            {hovered && (
              <span
                style={{
                  color: 'var(--color-text)',
                  fontWeight: 500,
                }}
              >
                {hovered.name}
              </span>
            )}
            <span
              className="truncate"
              style={{
                flex: 1,
                color: 'var(--color-text-tertiary)',
              }}
            >
              {hovered ? `— ${hint}` : hint}
            </span>
          </div>
        </div>
      )}
      <style>{`
        @keyframes tools-count-pulse {
          0% { transform: scale(1); }
          40% { transform: scale(1.18); }
          100% { transform: scale(1); }
        }
        .tools-count {
          display: inline-block;
          animation: tools-count-pulse 220ms ease-out;
        }
      `}</style>
    </div>
  );
}
