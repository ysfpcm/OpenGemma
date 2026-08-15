import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { AlertTriangle, Bot, CheckCircle2, ChevronLeft, Clock3, Info, Loader2, Search, Sparkles, X } from 'lucide-react';
import { useAppStore } from '../../lib/store';
import { createManagedAgent, fetchAvailableTools, fetchRecommendedModel, generateAgentConfig } from '../../lib/api';
import type { AgentTemplate, ToolInfo } from '../../lib/api';
import ToolsPicker from './ToolsPicker';

const DEFAULTS = {
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
  daily_briefing: 'Every morning, give me a concise briefing with my important messages, meetings, and local weather.',
  research_monitor: 'Search for the latest news and papers on the topic I provide. Summarize the most relevant findings and explain why they matter.',
  code_reviewer: 'Review the latest changes in the repository I provide. Check for bugs, security issues, and style violations, then summarize findings with file paths and line numbers.',
  meeting_prep: 'Before my next meeting, gather useful context from my messages, documents, and past meetings with the attendees. Summarize key topics and suggest talking points.',
  personal_deep_research: 'Search across my connected personal data to answer the question I provide. Cite the sources you used.',
  inbox_triager: 'Check my recent messages and email. Categorize them by priority and summarize the items that need my attention.',
  scout: 'Search the web for the latest updates on the topic I provide and compile a concise report with sources.',
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

function templateTools(template: AgentTemplate | null): string[] {
  const tools = template?.tools;
  return Array.isArray(tools) ? tools.filter((tool): tool is string => typeof tool === 'string') : [];
}

function templateInstruction(template: AgentTemplate): string {
  if (typeof template.instruction === 'string' && template.instruction.trim()) return template.instruction;
  return TEMPLATE_INSTRUCTIONS[template.id] || '';
}

function friendlyScheduleType(type: string | undefined, value = ''): string {
  if (type === 'interval') return 'hourly';
  if (type === 'cron' && /^0\s+\d+\s+\*\s+\*\s+\*$/.test(value)) return 'daily';
  if (type === 'cron' && /^0\s+\d+\s+\*\s+\*\s+[1-7](?:,[1-7])*$/.test(value)) return 'weekly';
  return type || 'manual';
}

function apiSchedule(type: string): string {
  if (type === 'daily' || type === 'weekly') return 'cron';
  if (type === 'hourly') return 'interval';
  return type;
}

function scheduleError(type: string, value: string): string | null {
  if (type === 'daily' || type === 'weekly' || type === 'cron') {
    if (!value.trim()) return 'Choose a time or enter a cron expression.';
  }
  if (type === 'hourly' && (!Number.isFinite(Number(value)) || Number(value) <= 0)) {
    return 'Set the interval to at least one hour.';
  }
  return null;
}

function unresolvedPlaceholders(instruction: string): string[] {
  return Array.from(instruction.matchAll(/\[([^\]]+)\]/g)).map((match) => match[1].trim()).filter(Boolean);
}

function hourOptions() {
  return Array.from({ length: 24 }, (_, hour) => {
    const label = hour === 0 ? '12 AM' : hour < 12 ? `${hour} AM` : hour === 12 ? '12 PM' : `${hour - 12} PM`;
    return <option key={hour} value={String(hour)}>{label}</option>;
  });
}

function cronHour(value: string): string {
  const match = value.match(/^0\s+(\d+)\s/);
  return match?.[1] || '9';
}

function cronDays(value: string): string[] {
  const match = value.match(/\*\s+\*\s+([^\s]+)$/);
  return match && match[1] !== '*' ? match[1].split(',') : [];
}

function clockLabel(hour: string): string {
  const numericHour = Number(hour);
  if (!Number.isFinite(numericHour)) return hour;
  if (numericHour === 0) return '12 AM';
  if (numericHour < 12) return `${numericHour} AM`;
  if (numericHour === 12) return '12 PM';
  return `${numericHour - 12} PM`;
}

function humanSchedule(type: string, value: string): string | null {
  if (type === 'daily') return `Every day at ${clockLabel(cronHour(value))}`;
  if (type === 'weekly') {
    const labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const days = cronDays(value).map((day) => labels[Number(day) - 1]).filter(Boolean);
    return days.length ? `Every ${days.join(', ')} at ${clockLabel(cronHour(value))}` : null;
  }
  const everyMinutes = value.match(/^\*\/(\d+)\s+(\d+)-(\d+)\s+\*\s+\*\s+\*$/);
  if (type === 'cron' && everyMinutes) return `Every ${everyMinutes[1]} minutes between ${clockLabel(everyMinutes[2])} and ${clockLabel(everyMinutes[3])}`;
  if (type === 'cron' && value.trim()) return `Preloaded schedule: ${value}`;
  if (type === 'hourly') return `Every ${Math.max(1, Math.round(Number(value || 3600) / 3600))} hours`;
  return null;
}

function Tooltip({ text }: { text: string }) {
  return <span className="inline-block ml-1 cursor-help" style={{ color: 'var(--color-text-tertiary)', fontSize: 10 }} title={text}>(?)</span>;
}

function TemplateCard({
  template,
  tools,
  toolsLoaded,
  onClick,
}: {
  template: AgentTemplate;
  tools: ToolInfo[];
  toolsLoaded: boolean;
  onClick: () => void;
}) {
  const names = templateTools(template);
  const toolMap = new Map(tools.map((tool) => [tool.name, tool]));
  const unavailable = names.filter((name) => !toolMap.get(name)?.configured);

  return (
    <button
      type="button"
      onClick={onClick}
      className="text-left p-4 rounded-xl transition-all flex flex-col min-h-[190px]"
      style={{ border: '1px solid var(--color-border)', background: 'var(--color-bg-secondary)' }}
      onMouseEnter={(event) => {
        event.currentTarget.style.borderColor = 'var(--color-accent)';
        event.currentTarget.style.background = 'color-mix(in srgb, var(--color-accent-purple) 6%, transparent)';
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.borderColor = 'var(--color-border)';
        event.currentTarget.style.background = 'var(--color-bg-secondary)';
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-lg" aria-hidden="true">{typeof template.icon === 'string' ? template.icon : '🤖'}</span>
          <span className="font-semibold text-sm truncate" style={{ color: 'var(--color-text)' }}>{template.name}</span>
        </div>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap" style={{ background: 'color-mix(in srgb, var(--color-success) 12%, transparent)', color: 'var(--color-success)' }}>
          Ready-made
        </span>
      </div>
      <div className="text-xs mt-3 leading-relaxed" style={{ color: 'var(--color-text-tertiary)' }}>{template.description}</div>
      <div className="flex flex-wrap gap-1 mt-auto pt-4">
        {names.slice(0, 4).map((name) => (
          <span key={name} className="text-[10px] px-1.5 py-1 rounded-md" title={toolMap.get(name)?.description || name} style={{ background: 'color-mix(in srgb, var(--color-accent-purple) 12%, transparent)', color: 'var(--color-accent-purple)' }}>
            {displayToolName(name)}
          </span>
        ))}
        {names.length > 4 && <span className="text-[10px] px-1.5 py-1" style={{ color: 'var(--color-text-tertiary)' }}>+{names.length - 4} more</span>}
      </div>
      <div className="flex items-center gap-1.5 mt-3 text-[10px]" style={{ color: unavailable.length && toolsLoaded ? 'var(--color-warning)' : 'var(--color-text-tertiary)' }}>
        {unavailable.length && toolsLoaded ? <AlertTriangle size={12} /> : <CheckCircle2 size={12} />}
        {toolsLoaded ? (unavailable.length ? `${unavailable.length} tool${unavailable.length === 1 ? '' : 's'} need setup` : 'Tools available') : 'Checking tools...'}
      </div>
    </button>
  );
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
  const models = useAppStore((state) => state.models);
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
    ...DEFAULTS,
  });
  const [launching, setLaunching] = useState(false);
  const [generatingConfig, setGeneratingConfig] = useState(false);
  const [recommendedModel, setRecommendedModel] = useState('');
  const [availableTools, setAvailableTools] = useState<ToolInfo[]>([]);
  const [toolsLoaded, setToolsLoaded] = useState(false);
  const [templateQuery, setTemplateQuery] = useState('');

  useEffect(() => {
    fetchRecommendedModel().then((result) => {
      setRecommendedModel(result.model);
      setWizard((current) => current.model ? current : ({ ...current, model: result.model }));
    }).catch(() => {});
    fetchAvailableTools().then(setAvailableTools).catch(() => {}).finally(() => setToolsLoaded(true));
  }, []);

  const filteredTemplates = useMemo(() => {
    const query = templateQuery.trim().toLowerCase();
    if (!query) return templates;
    return templates.filter((template) => `${template.name} ${template.description}`.toLowerCase().includes(query));
  }, [templateQuery, templates]);

  const selectedToolMap = useMemo(() => new Map(availableTools.map((tool) => [tool.name, tool])), [availableTools]);
  const missingSelectedTools = wizard.selectedTools.filter((name) => !selectedToolMap.get(name)?.configured);
  const placeholders = unresolvedPlaceholders(wizard.instruction);
  const currentScheduleError = scheduleError(wizard.scheduleType, wizard.scheduleValue);
  const scheduleDescription = humanSchedule(wizard.scheduleType, wizard.scheduleValue);
  const isCustom = !wizard.templateData;

  function selectTemplate(template: AgentTemplate | null) {
    if (!template) {
      setWizard((current) => ({ ...current, step: 2, templateId: '', templateData: null, name: '', instruction: '', scheduleType: 'manual', scheduleValue: '', selectedTools: [], ...DEFAULTS }));
      return;
    }
    const rawScheduleType = typeof template.schedule_type === 'string' ? template.schedule_type : undefined;
    const rawScheduleValue = typeof template.schedule_value === 'string' ? template.schedule_value : '';
    const scheduleType = friendlyScheduleType(rawScheduleType, rawScheduleValue);
    setWizard((current) => ({
      ...current,
      step: 2,
      templateId: template.id,
      templateData: template,
      name: template.name,
      instruction: templateInstruction(template),
      model: recommendedModel || current.model,
      scheduleType,
      scheduleValue: rawScheduleValue,
      selectedTools: templateTools(template),
      memoryExtraction: typeof template.memory_extraction === 'string' ? template.memory_extraction : DEFAULTS.memoryExtraction,
      observationCompression: typeof template.observation_compression === 'string' ? template.observation_compression : DEFAULTS.observationCompression,
      retrievalStrategy: typeof template.retrieval_strategy === 'string' ? template.retrieval_strategy : DEFAULTS.retrievalStrategy,
      taskDecomposition: typeof template.task_decomposition === 'string' ? template.task_decomposition : DEFAULTS.taskDecomposition,
      maxTurns: typeof template.max_turns === 'number' ? template.max_turns : DEFAULTS.maxTurns,
      temperature: typeof template.temperature === 'number' ? template.temperature : DEFAULTS.temperature,
    }));
  }

  async function handleAutoConfigure() {
    if (!wizard.instruction.trim()) {
      toast.error('Describe the agent first.');
      return;
    }
    setGeneratingConfig(true);
    try {
      const config = await generateAgentConfig(wizard.instruction);
      setWizard((current) => ({ ...current, name: config.name || current.name, instruction: config.instruction || current.instruction, selectedTools: config.tools || current.selectedTools }));
      toast.success('Draft configuration updated');
    } catch (error) {
      console.error(error);
      toast.error('Could not generate a configuration. You can still configure this agent manually.');
    } finally {
      setGeneratingConfig(false);
    }
  }

  async function handleLaunch() {
    if (!wizard.name.trim()) {
      toast.error('Give the agent a name first.');
      return;
    }
    if (currentScheduleError) {
      toast.error(currentScheduleError);
      return;
    }
    setLaunching(true);
    try {
      const config: Record<string, unknown> = {
        schedule_type: apiSchedule(wizard.scheduleType),
        schedule_value: wizard.scheduleValue || undefined,
        tools: wizard.selectedTools,
        memory_extraction: wizard.memoryExtraction,
        observation_compression: wizard.observationCompression,
        retrieval_strategy: wizard.retrievalStrategy,
        task_decomposition: wizard.taskDecomposition,
        max_turns: wizard.maxTurns,
        temperature: wizard.temperature,
      };
      if (wizard.budget) config.budget = Number(wizard.budget);
      if (wizard.instruction.trim()) config.instruction = wizard.instruction.trim();
      if (wizard.model) config.model = wizard.model;
      if (wizard.routerPolicy) config.router_policy = wizard.routerPolicy;

      await createManagedAgent({ name: wizard.name.trim(), template_id: wizard.templateId || undefined, config });
      toast.success(`Agent "${wizard.name.trim()}" is ready`);
      onLaunched();
    } catch (error: any) {
      toast.error(error.message || 'Failed to create agent');
    } finally {
      setLaunching(false);
    }
  }

  if (wizard.step === 1) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.68)', backdropFilter: 'blur(3px)' }}>
        <div className="rounded-2xl w-full max-w-5xl max-h-[calc(100vh-32px)] flex flex-col overflow-hidden" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', boxShadow: '0 24px 80px rgba(0,0,0,0.45)' }}>
          <div className="flex items-start justify-between gap-4 p-5 sm:p-6" style={{ borderBottom: '1px solid var(--color-border)' }}>
            <div>
              <div className="flex items-center gap-2">
                <Bot size={20} style={{ color: 'var(--color-accent)' }} />
                <h2 className="text-lg font-semibold" style={{ color: 'var(--color-text)' }}>Create an agent</h2>
              </div>
              <p className="text-sm mt-1" style={{ color: 'var(--color-text-secondary)' }}>Start with a workflow that already knows what to do. Review the defaults before launch.</p>
            </div>
            <button type="button" onClick={onClose} className="p-1 rounded" aria-label="Close" style={{ color: 'var(--color-text-tertiary)' }}><X size={18} /></button>
          </div>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-5 sm:px-6 py-4" style={{ background: 'var(--color-bg-secondary)' }}>
            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              <CheckCircle2 size={14} style={{ color: 'var(--color-success)' }} /> Templates include the schedule, behavior, and recommended tools.
            </div>
            <label className="relative w-full sm:w-64">
              <Search size={14} className="absolute left-2.5 top-2.5" style={{ color: 'var(--color-text-tertiary)' }} />
              <input value={templateQuery} onChange={(event) => setTemplateQuery(event.target.value)} placeholder="Find a template" className="w-full pl-8 pr-3 py-2 rounded-lg text-xs bg-transparent" style={{ border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
            </label>
          </div>
          <div className="overflow-y-auto p-5 sm:p-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {filteredTemplates.map((template) => <TemplateCard key={template.id} template={template} tools={availableTools} toolsLoaded={toolsLoaded} onClick={() => selectTemplate(template)} />)}
              <button type="button" onClick={() => selectTemplate(null)} className="text-left p-4 rounded-xl transition-all flex flex-col min-h-[190px]" style={{ border: '1px dashed var(--color-border)', background: 'transparent' }}>
                <div className="flex items-center gap-2"><Sparkles size={18} style={{ color: 'var(--color-accent)' }} /><span className="font-semibold text-sm" style={{ color: 'var(--color-text)' }}>Custom agent</span></div>
                <p className="text-xs mt-3 leading-relaxed" style={{ color: 'var(--color-text-tertiary)' }}>Describe the job in your own words. AI-assisted setup is optional, and every field remains editable.</p>
                <span className="text-xs mt-auto pt-4" style={{ color: 'var(--color-accent)' }}>Start from scratch →</span>
              </button>
            </div>
            {filteredTemplates.length === 0 && <div className="py-10 text-center text-sm" style={{ color: 'var(--color-text-tertiary)' }}>No templates match that search.</div>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.68)', backdropFilter: 'blur(3px)' }}>
      <div className="rounded-2xl w-full max-w-3xl max-h-[calc(100vh-32px)] flex flex-col overflow-hidden" style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', boxShadow: '0 24px 80px rgba(0,0,0,0.45)' }}>
        <div className="flex items-start justify-between gap-4 p-5 sm:p-6" style={{ borderBottom: '1px solid var(--color-border)' }}>
          <div className="flex items-start gap-2">
            <button type="button" onClick={() => setWizard((current) => ({ ...current, step: 1 }))} className="p-1 rounded mt-0.5" aria-label="Choose a different template" style={{ color: 'var(--color-text-tertiary)' }}><ChevronLeft size={18} /></button>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-lg" aria-hidden="true">{typeof wizard.templateData?.icon === 'string' ? wizard.templateData.icon : '🤖'}</span>
                <h2 className="text-lg font-semibold" style={{ color: 'var(--color-text)' }}>{wizard.templateData?.name || 'Custom agent'}</h2>
              </div>
              <p className="text-sm mt-1" style={{ color: 'var(--color-text-secondary)' }}>{wizard.templateData?.description || 'Set the job, tools, and schedule for a new agent.'}</p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-1 rounded" aria-label="Close" style={{ color: 'var(--color-text-tertiary)' }}><X size={18} /></button>
        </div>

        <div className="overflow-y-auto p-5 sm:p-6 space-y-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1" style={{ color: 'var(--color-text-secondary)' }}>Agent name</label>
              <input value={wizard.name} onChange={(event) => setWizard((current) => ({ ...current, name: event.target.value }))} placeholder="e.g. Morning commute" autoFocus={!wizard.name} className="w-full px-3 py-2.5 rounded-lg text-sm bg-transparent" style={{ border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1" style={{ color: 'var(--color-text-secondary)' }}>Intelligence</label>
              <select value={wizard.model} onChange={(event) => setWizard((current) => ({ ...current, model: event.target.value }))} className="w-full px-3 py-2.5 rounded-lg text-sm" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
                {models.length === 0 && <option value={wizard.model}>{wizard.model || 'Use the configured model'}</option>}
                {models.map((model) => <option key={model.id} value={model.id}>{model.id}{model.id === recommendedModel ? ' (recommended)' : ''}</option>)}
              </select>
            </div>
          </div>

          <div>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-1">
              <label className="block text-sm font-medium" style={{ color: 'var(--color-text-secondary)' }}>What should it do?</label>
              {isCustom && <button type="button" onClick={handleAutoConfigure} disabled={generatingConfig} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md disabled:opacity-50 self-start" style={{ background: 'color-mix(in srgb, var(--color-accent) 12%, transparent)', border: '1px solid color-mix(in srgb, var(--color-accent) 25%, transparent)', color: 'var(--color-accent)' }}><Sparkles size={12} /> {generatingConfig ? 'Drafting...' : 'Optional AI setup'}</button>}
            </div>
            <textarea value={wizard.instruction} onChange={(event) => setWizard((current) => ({ ...current, instruction: event.target.value }))} placeholder="Describe the result you want in plain language." rows={4} className="w-full px-3 py-2.5 rounded-lg text-sm bg-transparent resize-y" style={{ border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />
            {placeholders.length > 0 && <div className="flex items-start gap-2 mt-2 p-3 rounded-lg text-xs" style={{ background: 'color-mix(in srgb, var(--color-warning) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--color-warning) 25%, transparent)', color: 'var(--color-warning)' }}><AlertTriangle size={14} className="mt-0.5 shrink-0" /><span>Before this agent can act on your behalf, replace: {placeholders.map((value) => `[${value}]`).join(', ')}. You can launch now and finish setup later.</span></div>}
          </div>

          <ToolsPicker tools={availableTools} selected={wizard.selectedTools} onChange={(selectedTools) => setWizard((current) => ({ ...current, selectedTools }))} />

          {missingSelectedTools.length > 0 && toolsLoaded && <div className="flex items-start gap-2 p-3 rounded-lg text-xs" style={{ background: 'color-mix(in srgb, var(--color-warning) 8%, transparent)', border: '1px solid color-mix(in srgb, var(--color-warning) 20%, transparent)', color: 'var(--color-text-secondary)' }}><Info size={14} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warning)' }} /><span><strong style={{ color: 'var(--color-warning)' }}>{missingSelectedTools.length} selected tool{missingSelectedTools.length === 1 ? '' : 's'} need setup.</strong> The agent can still be created; connect the related service before its first run.</span></div>}

          <div>
            <div className="flex items-center gap-2 mb-1"><label className="block text-sm font-medium" style={{ color: 'var(--color-text-secondary)' }}>Schedule</label><Tooltip text="Manual agents run when you press Run Now or send them a message. A scheduled agent runs automatically." /></div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <select value={wizard.scheduleType} onChange={(event) => setWizard((current) => ({ ...current, scheduleType: event.target.value, scheduleValue: event.target.value === 'manual' ? '' : current.scheduleValue }))} className="w-full px-3 py-2.5 rounded-lg text-sm" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
                <option value="manual">Manual — run on demand</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="hourly">Every N hours</option>
                <option value="cron">Custom cron expression</option>
              </select>
              {wizard.scheduleType === 'manual' && <div className="flex items-center gap-2 px-3 py-2 text-xs rounded-lg" style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-tertiary)' }}><Clock3 size={14} /> Runs when you ask it to.</div>}
              {wizard.scheduleType === 'daily' && <select value={cronHour(wizard.scheduleValue)} onChange={(event) => setWizard((current) => ({ ...current, scheduleValue: `0 ${event.target.value} * * *` }))} className="w-full px-3 py-2.5 rounded-lg text-sm" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>{hourOptions()}</select>}
              {wizard.scheduleType === 'weekly' && <div className="space-y-2"><div className="flex flex-wrap gap-1">{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day, index) => { const dayNumber = String(index + 1); const selected = cronDays(wizard.scheduleValue).includes(dayNumber); return <button key={day} type="button" onClick={() => { const days = cronDays(wizard.scheduleValue); const nextDays = (selected ? days.filter((item) => item !== dayNumber) : [...days, dayNumber]).sort(); setWizard((current) => ({ ...current, scheduleValue: nextDays.length ? `0 ${cronHour(current.scheduleValue)} * * ${nextDays.join(',')}` : '' })); }} className="px-2 py-1.5 rounded text-xs" style={{ background: selected ? 'var(--color-accent)' : 'var(--color-bg-secondary)', color: selected ? 'var(--color-on-accent)' : 'var(--color-text-tertiary)', border: `1px solid ${selected ? 'var(--color-accent)' : 'var(--color-border)'}` }}>{day}</button>; })}</div><select value={cronHour(wizard.scheduleValue)} onChange={(event) => setWizard((current) => ({ ...current, scheduleValue: `0 ${event.target.value} * * ${cronDays(current.scheduleValue).join(',') || '1'}` }))} className="w-full px-3 py-2 rounded-lg text-xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>{hourOptions()}</select></div>}
              {wizard.scheduleType === 'hourly' && <div className="flex items-center gap-2"><span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>Every</span><input type="number" min="1" max="24" value={Math.max(1, Math.round(Number(wizard.scheduleValue || 3600) / 3600))} onChange={(event) => setWizard((current) => ({ ...current, scheduleValue: String(Math.min(24, Math.max(1, Number(event.target.value) || 1)) * 3600) }))} className="w-20 px-2 py-2 rounded-lg text-sm text-center" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} /><span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>hours</span></div>}
              {wizard.scheduleType === 'cron' && <input value={wizard.scheduleValue} onChange={(event) => setWizard((current) => ({ ...current, scheduleValue: event.target.value }))} placeholder="0 9 * * *" className="w-full px-3 py-2.5 rounded-lg text-sm bg-transparent" style={{ border: '1px solid var(--color-border)', color: 'var(--color-text)' }} />}
            </div>
            {scheduleDescription && <div className="flex items-center gap-2 mt-2 text-xs" style={{ color: 'var(--color-text-tertiary)' }}><Clock3 size={13} style={{ color: 'var(--color-accent)' }} />{scheduleDescription}</div>}
            {currentScheduleError && <p className="text-xs mt-1.5" style={{ color: 'var(--color-warning)' }}>{currentScheduleError}</p>}
          </div>

          <details className="rounded-lg" style={{ border: '1px solid var(--color-border)' }}>
            <summary className="px-3 py-2.5 cursor-pointer text-sm font-medium" style={{ color: 'var(--color-text-secondary)' }}>Advanced settings <span className="text-xs font-normal" style={{ color: 'var(--color-text-tertiary)' }}>(optional)</span></summary>
            <div className="px-3 pb-3 pt-3 space-y-3" style={{ borderTop: '1px solid var(--color-border)' }}>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div><label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Memory extraction<Tooltip text="How the agent remembers context between runs." /></label><select value={wizard.memoryExtraction} onChange={(event) => setWizard((current) => ({ ...current, memoryExtraction: event.target.value }))} className="w-full px-2 py-2 rounded text-xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}><option value="structured_json">Structured JSON</option><option value="causality_graph">Causality graph</option><option value="scratchpad">Scratchpad</option><option value="none">None</option></select></div>
                <div><label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Observation compression<Tooltip text="How long tool output is summarized." /></label><select value={wizard.observationCompression} onChange={(event) => setWizard((current) => ({ ...current, observationCompression: event.target.value }))} className="w-full px-2 py-2 rounded text-xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}><option value="summarize">Summarize</option><option value="truncate">Truncate</option><option value="none">None</option></select></div>
                <div><label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Retrieval strategy<Tooltip text="How the agent searches connected knowledge." /></label><select value={wizard.retrievalStrategy} onChange={(event) => setWizard((current) => ({ ...current, retrievalStrategy: event.target.value }))} className="w-full px-2 py-2 rounded text-xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}><option value="sqlite">BM25 (SQLite)</option><option value="hybrid">Hybrid</option><option value="colbert">ColBERT</option><option value="keyword">Keyword</option><option value="none">None</option></select></div>
                <div><label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Task decomposition<Tooltip text="How the agent breaks a complex task into steps." /></label><select value={wizard.taskDecomposition} onChange={(event) => setWizard((current) => ({ ...current, taskDecomposition: event.target.value }))} className="w-full px-2 py-2 rounded text-xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}><option value="hierarchical">Hierarchical</option><option value="phased">Phased</option><option value="monolithic">Monolithic</option></select></div>
                <div><label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Max turns</label><input type="number" min="1" value={wizard.maxTurns} onChange={(event) => setWizard((current) => ({ ...current, maxTurns: Math.max(1, Number(event.target.value) || DEFAULTS.maxTurns) }))} className="w-full px-2 py-2 rounded text-xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} /></div>
                <div><label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Temperature</label><input type="number" step="0.1" min="0" max="2" value={wizard.temperature} onChange={(event) => setWizard((current) => ({ ...current, temperature: Math.min(2, Math.max(0, Number(event.target.value) || DEFAULTS.temperature)) }))} className="w-full px-2 py-2 rounded text-xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} /></div>
                <div><label className="block text-xs mb-1" style={{ color: 'var(--color-text-tertiary)' }}>Budget ($)</label><input type="number" step="0.01" min="0" value={wizard.budget} onChange={(event) => setWizard((current) => ({ ...current, budget: event.target.value }))} placeholder="Unlimited" className="w-full px-2 py-2 rounded text-xs" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }} /></div>
              </div>
            </div>
          </details>
        </div>

        <div className="flex flex-col-reverse sm:flex-row gap-3 p-5 sm:p-6" style={{ borderTop: '1px solid var(--color-border)' }}>
          <button type="button" onClick={onClose} className="px-4 py-2.5 rounded-lg text-sm" style={{ border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}>Cancel</button>
          <button type="button" onClick={handleLaunch} disabled={launching || !wizard.name.trim() || !!currentScheduleError} className="flex-1 inline-flex justify-center items-center gap-2 py-2.5 rounded-lg text-sm font-semibold disabled:opacity-50" style={{ background: 'var(--color-accent)', color: 'var(--color-on-accent)' }}>{launching && <Loader2 size={15} className="animate-spin" />}{launching ? 'Creating agent...' : 'Launch agent'}</button>
        </div>
      </div>
    </div>
  );
}
