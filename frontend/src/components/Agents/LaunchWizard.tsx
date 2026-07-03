import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { useAppStore } from '../../lib/store';
import { generateAgentConfig, createManagedAgent, fetchRecommendedModel, fetchAvailableTools } from '../../lib/api';
import type { AgentTemplate, ToolInfo } from '../../lib/api';
import ToolsPicker from './ToolsPicker';
import { Bot, ChevronRight, ChevronLeft, X, ListTodo, Plus, Loader2 } from 'lucide-react';

const UNIVERSAL_DEFAULTS = {
  memoryExtraction: 'structured_json',
  observationCompression: 'summarize',
  retrievalStrategy: 'sqlite',
  taskDecomposition: 'hierarchical',
  maxTurns: 25,
  temperature: 0.3,
};

export interface WizardState {
  step: 1 | 2;
  templateId: string;
  templateData: AgentTemplate | null;
  name: string;
  instruction: string;
  model: string;
  scheduleType: string;
  scheduleValue: string;
  selectedTools: string[];
  budget: string;
  routerPolicy: string;
  memoryExtraction: string;
  observationCompression: string;
  retrievalStrategy: string;
  taskDecomposition: string;
  maxTurns: number;
  temperature: number;
}

const TEMPLATE_INSTRUCTIONS: Record<string, string> = {
  'daily-briefing': 'Every morning, give me a fun quote of the day, summarize my top important emails, list any meetings today from my calendar, and tell me the weather for [my city].',
  'daily_briefing': 'Every morning, give me a fun quote of the day, summarize my top important emails, list any meetings today from my calendar, and tell me the weather for [my city].',
  'research-monitor': 'Search for the latest news and papers on [your topic]. Summarize the top 3 most relevant findings and explain why they matter.',
  'research_monitor': 'Search for the latest news and papers on [your topic]. Summarize the top 3 most relevant findings and explain why they matter.',
  'code-reviewer': 'Review the latest commits in [repo]. Check for bugs, security issues, and style violations. Summarize findings with file paths and line numbers.',
  'code_reviewer': 'Review the latest commits in [repo]. Check for bugs, security issues, and style violations. Summarize findings with file paths and line numbers.',
  'meeting-prep': 'Before my next meeting, pull context from my emails, messages, and past meetings with the attendees. Summarize key topics and suggest talking points.',
  'meeting_prep': 'Before my next meeting, pull context from my emails, messages, and past meetings with the attendees. Summarize key topics and suggest talking points.',
  'personal_deep_research': 'Search across all my personal data — messages, emails, meetings, documents, and notes — to answer [my question]. Cite your sources.',
  'inbox_triager': 'Check my recent emails and messages. Categorize them by priority (urgent, important, FYI, spam). Summarize the top items I should act on.',
  'scout': 'Search the web for the latest updates on [your topic] and compile a detailed research report listing key findings and sources.',
};

function Tooltip({ text }: { text: string }) {
  return <span className="inline-block ml-1 cursor-help" style={{ color: 'var(--color-text-tertiary)', fontSize: 10 }} title={text}>(?)</span>;
}

export default function LaunchWizard({
  templates,
  onClose,
  onLaunched,
}: {
  templates: AgentTemplate[];
  onClose: () => void;
  onLaunched: () => void;
}) {
  const UNIVERSAL_DEFAULTS = {
    memoryExtraction: 'structured_json',
    observationCompression: 'summarize',
    retrievalStrategy: 'sqlite',
    taskDecomposition: 'hierarchical',
    maxTurns: 25,
    temperature: 0.3,
  };

  const [wizard, setWizard] = useState<WizardState>({
    step: 1,
    templateId: '',
    templateData: null,
    name: '',
    instruction: '',
    model: '',
    scheduleType: 'manual',
    scheduleValue: '',
    selectedTools: [],
    budget: '',
    routerPolicy: '',
    ...UNIVERSAL_DEFAULTS,
  });
  const [launching, setLaunching] = useState(false);
  const [generatingConfig, setGeneratingConfig] = useState(false);
  const [recommendedModel, setRecommendedModel] = useState('');
  const [availableTools, setAvailableTools] = useState<ToolInfo[]>([]);
  const models = useAppStore((s) => s.models);

  async function handleAutoConfigure() {
    if (!wizard.instruction.trim()) {
      toast.error('Please describe what the agent should do first.');
      return;
    }
    setGeneratingConfig(true);
    try {
      const config = await generateAgentConfig(wizard.instruction);
      setWizard((w) => ({
        ...w,
        name: config.name || w.name,
        instruction: config.instruction || w.instruction,
        selectedTools: config.tools || w.selectedTools,
      }));
      toast.success('Agent auto-configured successfully!');
    } catch (err) {
      console.error(err);
      toast.error('Failed to generate agent configuration.');
    } finally {
      setGeneratingConfig(false);
    }
  }

  useEffect(() => {
    fetchRecommendedModel().then((r) => {
      setRecommendedModel(r.model);
      if (!wizard.model) {
        setWizard((w) => ({ ...w, model: r.model }));
      }
    }).catch(() => {});
    fetchAvailableTools().then((tools) => {
      setAvailableTools(tools);
    }).catch(() => {});
  }, []);

  function selectTemplate(tpl: AgentTemplate | null) {
    if (tpl) {
      setWizard((w) => ({
        ...w,
        step: 2,
        templateId: tpl.id,
        templateData: tpl,
        name: '',
        instruction: (tpl as any).instruction || TEMPLATE_INSTRUCTIONS[tpl.id] || '',
        model: recommendedModel || w.model,
        scheduleType: (tpl as any).schedule_type || 'manual',
        scheduleValue: (tpl as any).schedule_value || '',
        selectedTools: (tpl as any).tools || [],
        memoryExtraction: (tpl as any).memory_extraction || UNIVERSAL_DEFAULTS.memoryExtraction,
        observationCompression: (tpl as any).observation_compression || UNIVERSAL_DEFAULTS.observationCompression,
        retrievalStrategy: (tpl as any).retrieval_strategy || UNIVERSAL_DEFAULTS.retrievalStrategy,
        taskDecomposition: (tpl as any).task_decomposition || UNIVERSAL_DEFAULTS.taskDecomposition,
        maxTurns: (tpl as any).max_turns || UNIVERSAL_DEFAULTS.maxTurns,
        temperature: (tpl as any).temperature ?? UNIVERSAL_DEFAULTS.temperature,
      }));
    } else {
      setWizard((w) => ({
        ...w,
        step: 2,
        templateId: '',
        templateData: null,
        name: '',
        instruction: '',
        model: recommendedModel || w.model,
        scheduleType: 'manual',
        scheduleValue: '',
        selectedTools: [],
        ...UNIVERSAL_DEFAULTS,
      }));
    }
  }

  async function handleLaunch() {
    if (!wizard.name.trim()) { toast.error('Name is required'); return; }
    setLaunching(true);
    try {
      // Map friendly schedule presets to API schedule_type/schedule_value
      let apiScheduleType = wizard.scheduleType;
      let apiScheduleValue = wizard.scheduleValue;
      if (wizard.scheduleType === 'daily' || wizard.scheduleType === 'weekly') {
        apiScheduleType = 'cron';
        // scheduleValue already holds the cron expression
      } else if (wizard.scheduleType === 'hourly') {
        apiScheduleType = 'interval';
        // scheduleValue already holds seconds as string
      }

      const config: Record<string, unknown> = {
        schedule_type: apiScheduleType,
        schedule_value: apiScheduleValue || undefined,
        tools: wizard.selectedTools,
        learning_enabled: !!wizard.routerPolicy,
        memory_extraction: wizard.memoryExtraction,
        observation_compression: wizard.observationCompression,
        retrieval_strategy: wizard.retrievalStrategy,
        task_decomposition: wizard.taskDecomposition,
        max_turns: wizard.maxTurns,
        temperature: wizard.temperature,
      };
      if (wizard.budget) config.budget = parseFloat(wizard.budget);
      if (wizard.instruction.trim()) config.instruction = wizard.instruction.trim();
      if (wizard.model) config.model = wizard.model;
      if (wizard.routerPolicy) config.router_policy = wizard.routerPolicy;

      await createManagedAgent({
        name: wizard.name.trim(),
        template_id: wizard.templateId || undefined,
        config,
      });
      toast.success(`Agent "${wizard.name}" created`);
      onLaunched();
    } catch (err: any) {
      toast.error(err.message || 'Failed to create agent');
    } finally {
      setLaunching(false);
    }
  }

  const formatScheduleLabel = (type: string, value: string) => {
    if (type === 'manual') return 'Manual (run on demand)';
    if (type === 'cron') return `Cron: ${value}`;
    if (type === 'interval') {
      const secs = parseInt(value, 10);
      if (secs >= 3600) return `Every ${secs / 3600}h`;
      if (secs >= 60) return `Every ${secs / 60}m`;
      return `Every ${secs}s`;
    }
    return type;
  };

  // ── Step 1: Template Selection ──
  if (wizard.step === 1) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.6)' }}>
        <div className="rounded-xl p-6 w-full max-w-lg" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}>
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold" style={{ color: 'var(--color-text)' }}>New Agent — Choose Template</h2>
            <button onClick={onClose} className="p-1 rounded hover:bg-opacity-10" style={{ color: 'var(--color-text-tertiary)' }}><X size={18} /></button>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {templates.map((tpl) => (
              <button
                key={tpl.id}
                onClick={() => selectTemplate(tpl)}
                className="text-left p-4 rounded-lg transition-all items-start"
                style={{ border: '1px solid var(--color-border)', background: 'var(--color-bg-secondary)' }}
                onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--color-accent)'; e.currentTarget.style.background = 'color-mix(in srgb, var(--color-accent-purple) 6%, transparent)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--color-border)'; e.currentTarget.style.background = 'var(--color-bg-secondary)'; }}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-lg">{(tpl as any).icon || '🤖'}</span>
                  <span className="font-semibold text-sm" style={{ color: 'var(--color-text)' }}>{tpl.name}</span>
                </div>
                <div className="text-xs mt-1" style={{ color: 'var(--color-text-tertiary)', textAlign: 'left' }}>{tpl.description}</div>
                {(tpl as any).tools && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {((tpl as any).tools as string[]).slice(0, 4).map((t: string) => (
                      <span key={t} className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'color-mix(in srgb, var(--color-accent-purple) 12%, transparent)', color: 'var(--color-accent-purple)' }}>{t}</span>
                    ))}
                    {((tpl as any).tools as string[]).length > 4 && (
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ color: 'var(--color-text-tertiary)' }}>+{((tpl as any).tools as string[]).length - 4}</span>
                    )}
                  </div>
                )}
              </button>
            ))}
            <button
              onClick={() => selectTemplate(null)}
              className="text-left p-4 rounded-lg transition-all items-start"
              style={{ border: '1px solid var(--color-border)', background: 'var(--color-bg-secondary)' }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--color-accent)'; e.currentTarget.style.background = 'color-mix(in srgb, var(--color-accent-purple) 6%, transparent)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--color-border)'; e.currentTarget.style.background = 'var(--color-bg-secondary)'; }}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-lg">⚙️</span>
                <span className="font-semibold text-sm" style={{ color: 'var(--color-text)' }}>Custom Agent</span>
              </div>
              <div className="text-xs mt-1" style={{ color: 'var(--color-text-tertiary)', textAlign: 'left' }}>Start from scratch. Pick your own tools, schedule, and behavior.</div>
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Step 2: Configuration ──
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.6)' }}>
      <div className="rounded-xl p-6 w-full max-w-lg max-h-[85vh] overflow-y-auto" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}>
        <div className="flex justify-between items-center mb-4">
          <div className="flex items-center gap-2">
            <button onClick={() => setWizard((w) => ({ ...w, step: 1 }))} className="p-1 rounded" style={{ color: 'var(--color-text-tertiary)' }}><ChevronLeft size={18} /></button>
            <h2 className="text-lg font-semibold" style={{ color: 'var(--color-text)' }}>
              {wizard.templateData ? `New ${wizard.templateData.name}` : 'New Custom Agent'}
            </h2>
          </div>
          <button onClick={onClose} className="p-1 rounded" style={{ color: 'var(--color-text-tertiary)' }}><X size={18} /></button>
        </div>

        <div className="space-y-4">
          {/* Name */}
          <div>
            <label className="block text-sm font-medium mb-1" style={{ color: 'var(--color-text-secondary)' }}>Agent Name</label>
            <input
              value={wizard.name}
              onChange={(e) => setWizard((w) => ({ ...w, name: e.target.value }))}
              placeholder="e.g. AI Research Tracker"
              className="w-full px-3 py-2 rounded-lg text-sm bg-transparent"
              style={{ border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
            />
          </div>

          {/* Instruction */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <label className="block text-sm font-medium" style={{ color: 'var(--color-text-secondary)' }}>What should this agent do?</label>
              <button
                type="button"
                onClick={handleAutoConfigure}
                disabled={generatingConfig}
                className="text-xs px-2 py-0.5 rounded hover:bg-opacity-20 disabled:opacity-50"
                style={{
                  background: 'color-mix(in srgb, var(--color-accent) 15%, transparent)',
                  border: '1px solid color-mix(in srgb, var(--color-accent) 30%, transparent)',
                  color: 'var(--color-accent)',
                  cursor: generatingConfig ? 'not-allowed' : 'pointer'
                }}
              >
                {generatingConfig ? '🪄 Configuring...' : '🪄 Auto-Configure with AI'}
              </button>
            </div>
            <textarea
              value={wizard.instruction}
              onChange={(e) => setWizard((w) => ({ ...w, instruction: e.target.value }))}
              placeholder="e.g. Monitor the latest research papers on reasoning and chain-of-thought in LLMs"
              rows={3}
              className="w-full px-3 py-2 rounded-lg text-sm bg-transparent resize-none"
              style={{ border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
            />
            {wizard.instruction.includes('[') && (
              <p className="text-[10px] mt-1" style={{ color: 'var(--color-warning)' }}>
                Replace the [bracketed text] with your own values
              </p>
            )}
          </div>

          {/* Tools picker */}
          <ToolsPicker
            tools={availableTools}
            selected={wizard.selectedTools}
            onChange={(next) =>
              setWizard((w) => ({ ...w, selectedTools: next }))
            }
          />

          {/* Model + Schedule row */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium mb-1" style={{ color: 'var(--color-text-secondary)' }}>Intelligence</label>
              <select
                value={wizard.model}
                onChange={(e) => setWizard((w) => ({ ...w, model: e.target.value }))}
                className="w-full px-3 py-2 rounded-lg text-sm"
                style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
              >
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id}{m.id === recommendedModel ? ' (recommended)' : ''}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1" style={{ color: 'var(--color-text-secondary)' }}>Schedule</label>
              <select
                value={wizard.scheduleType}
                onChange={(e) => setWizard((w) => ({ ...w, scheduleType: e.target.value, scheduleValue: e.target.value === 'manual' ? '' : w.scheduleValue }))}
                className="w-full px-3 py-2 rounded-lg text-sm"
                style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
              >
                <option value="manual">Manual (run on demand)</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="hourly">Every N hours</option>
                <option value="cron">Custom (cron expression)</option>
              </select>
              {wizard.scheduleType === 'daily' && (
                <select
                  value={(() => { const m = wizard.scheduleValue.match(/^0\s+(\d+)\s/); return m ? m[1] : '9'; })()}
                  onChange={(e) => setWizard((w) => ({ ...w, scheduleValue: `0 ${e.target.value} * * *` }))}
                  className="w-full px-3 py-1.5 rounded-lg text-xs mt-1.5"
                  style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
                >
                  {Array.from({ length: 24 }, (_, i) => {
                    const label = i === 0 ? '12 AM' : i < 12 ? `${i} AM` : i === 12 ? '12 PM' : `${i - 12} PM`;
                    return <option key={i} value={String(i)}>{label}</option>;
                  })}
                </select>
              )}
              {wizard.scheduleType === 'weekly' && (
                <div className="mt-1.5 space-y-1.5">
                  <div className="flex gap-1">
                    {(['Mon','Tue','Wed','Thu','Fri','Sat','Sun'] as const).map((day, idx) => {
                      const dayNum = String(idx + 1);
                      const cronParts = wizard.scheduleValue.match(/\*\s+\*\s+(.+)$/);
                      const selectedDays = cronParts ? cronParts[1].split(',') : [];
                      const isSelected = selectedDays.includes(dayNum);
                      return (
                        <button
                          key={day}
                          type="button"
                          onClick={() => {
                            const newDays = isSelected ? selectedDays.filter(d => d !== dayNum) : [...selectedDays, dayNum].sort();
                            const hourMatch = wizard.scheduleValue.match(/^0\s+(\d+)\s/);
                            const hour = hourMatch ? hourMatch[1] : '9';
                            setWizard((w) => ({ ...w, scheduleValue: newDays.length > 0 ? `0 ${hour} * * ${newDays.join(',')}` : '' }));
                          }}
                          className="px-1.5 py-1 rounded text-xs font-medium"
                          style={{
                            background: isSelected ? 'var(--color-accent)' : 'var(--color-bg)',
                            color: isSelected ? 'var(--color-on-accent)' : 'var(--color-text-tertiary)',
                            border: `1px solid ${isSelected ? 'var(--color-accent)' : 'var(--color-border)'}`,
                          }}
                        >
                          {day}
                        </button>
                      );
                    })}
                  </div>
                  <select
                    value={(() => { const m = wizard.scheduleValue.match(/^0\s+(\d+)\s/); return m ? m[1] : '9'; })()}
                    onChange={(e) => {
                      const cronParts = wizard.scheduleValue.match(/\*\s+\*\s+(.+)$/);
                      const days = cronParts ? cronParts[1] : '1';
                      setWizard((w) => ({ ...w, scheduleValue: `0 ${e.target.value} * * ${days}` }));
                    }}
                    className="w-full px-3 py-1.5 rounded-lg text-xs"
                    style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
                  >
                    {Array.from({ length: 24 }, (_, i) => {
                      const label = i === 0 ? '12 AM' : i < 12 ? `${i} AM` : i === 12 ? '12 PM' : `${i - 12} PM`;
                      return <option key={i} value={String(i)}>{label}</option>;
                    })}
                  </select>
                </div>
              )}
              {wizard.scheduleType === 'hourly' && (
                <div className="flex items-center gap-2 mt-1.5">
                  <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>Every</span>
                  <input
                    type="number" min="1" max="24"
                    value={(() => { const secs = parseInt(wizard.scheduleValue || '0', 10); return secs > 0 ? Math.round(secs / 3600) : 1; })()}
                    onChange={(e) => {
                      const hrs = Math.min(24, Math.max(1, parseInt(e.target.value, 10) || 1));
                      setWizard((w) => ({ ...w, scheduleValue: String(hrs * 3600) }));
                    }}
                    className="w-14 px-2 py-1 rounded text-xs text-center"
                    style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
                  />
                  <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>hours</span>
                </div>
              )}
              {wizard.scheduleType === 'cron' && (
                <input
                  value={wizard.scheduleValue}
                  onChange={(e) => setWizard((w) => ({ ...w, scheduleValue: e.target.value }))}
                  placeholder="0 9 * * *"
                  className="w-full px-3 py-1.5 rounded-lg text-xs bg-transparent mt-1.5"
                  style={{ border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
                />
              )}
            </div>
          </div>

          {/* Tools tags */}
          {wizard.selectedTools.length > 0 && (
            <div>
              <label className="block text-sm font-medium mb-1" style={{ color: 'var(--color-text-secondary)' }}>
                Tools <span style={{ color: 'var(--color-text-tertiary)', fontWeight: 400 }}>(from template)</span>
              </label>
              <div className="flex flex-wrap gap-1.5">
                {wizard.selectedTools.map((t) => (
                  <span key={t} className="text-xs px-2 py-1 rounded" style={{ background: 'color-mix(in srgb, var(--color-accent-purple) 12%, transparent)', color: 'var(--color-accent-purple)' }}>{t}</span>
                ))}
              </div>
            </div>
          )}

          {/* Advanced Settings */}
          <details className="rounded-lg" style={{ border: '1px solid var(--color-border)' }}>
            <summary className="px-3 py-2 cursor-pointer text-sm font-medium" style={{ color: 'var(--color-text-tertiary)' }}>
              Advanced Settings <span className="text-xs font-normal">(optional)</span>
            </summary>
            <div className="px-3 pb-3 pt-1 space-y-3" style={{ borderTop: '1px solid var(--color-border)' }}>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Memory Extraction<Tooltip text="How the agent remembers context between runs" /></label>
                  <select value={wizard.memoryExtraction} onChange={(e) => setWizard((w) => ({ ...w, memoryExtraction: e.target.value }))}
                    className="w-full px-2 py-1 rounded text-xs" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
                    <option value="structured_json">Structured JSON</option>
                    <option value="causality_graph">Causality Graph</option>
                    <option value="scratchpad">Scratchpad</option>
                    <option value="none">None</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Observation Compression<Tooltip text="How the agent summarizes long tool outputs" /></label>
                  <select value={wizard.observationCompression} onChange={(e) => setWizard((w) => ({ ...w, observationCompression: e.target.value }))}
                    className="w-full px-2 py-1 rounded text-xs" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
                    <option value="summarize">Summarize</option>
                    <option value="truncate">Truncate</option>
                    <option value="none">None</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Retrieval Strategy<Tooltip text="How the agent searches your knowledge base" /></label>
                  <select value={wizard.retrievalStrategy} onChange={(e) => setWizard((w) => ({ ...w, retrievalStrategy: e.target.value }))}
                    className="w-full px-2 py-1 rounded text-xs" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
                    <option value="sqlite">BM25 (SQLite FTS5)</option>
                    <option value="hybrid">Hybrid (BM25 + Semantic)</option>
                    <option value="colbert">ColBERTv2</option>
                    <option value="none">None</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Task Decomposition<Tooltip text="How the agent breaks complex tasks into steps" /></label>
                  <select value={wizard.taskDecomposition} onChange={(e) => setWizard((w) => ({ ...w, taskDecomposition: e.target.value }))}
                    className="w-full px-2 py-1 rounded text-xs" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
                    <option value="hierarchical">Hierarchical</option>
                    <option value="phased">Phased</option>
                    <option value="monolithic">Monolithic</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Max Turns</label>
                  <input type="number" value={wizard.maxTurns} onChange={(e) => setWizard((w) => ({ ...w, maxTurns: parseInt(e.target.value, 10) || 25 }))}
                    className="w-full px-2 py-1 rounded text-xs" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
                </div>
                <div>
                  <label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Temperature</label>
                  <input type="number" step="0.1" min="0" max="2" value={wizard.temperature}
                    onChange={(e) => setWizard((w) => ({ ...w, temperature: parseFloat(e.target.value) || 0.3 }))}
                    className="w-full px-2 py-1 rounded text-xs" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
                </div>
                <div>
                  <label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Budget ($)</label>
                  <input type="number" step="0.01" value={wizard.budget} onChange={(e) => setWizard((w) => ({ ...w, budget: e.target.value }))}
                    placeholder="Unlimited"
                    className="w-full px-2 py-1 rounded text-xs" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
                </div>
                <div>
                  <label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Schedule Type</label>
                  <select value={wizard.scheduleType} onChange={(e) => setWizard((w) => ({ ...w, scheduleType: e.target.value, scheduleValue: e.target.value === 'manual' ? '' : w.scheduleValue }))}
                    className="w-full px-2 py-1 rounded text-xs" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
                    <option value="manual">Manual</option>
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly</option>
                    <option value="hourly">Every N hours</option>
                    <option value="cron">Custom (cron)</option>
                  </select>
                </div>
              </div>
            </div>
          </details>

          {/* Launch */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={handleLaunch}
              disabled={launching || !wizard.name.trim()}
              className="flex-1 py-2.5 rounded-lg text-sm font-semibold"
              style={{ background: 'var(--color-accent)', color: 'var(--color-on-accent)', opacity: launching || !wizard.name.trim() ? 0.5 : 1 }}
            >
              {launching ? 'Creating...' : 'Launch Agent'}
            </button>
            <button onClick={onClose} className="px-4 py-2.5 rounded-lg text-sm" style={{ border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
