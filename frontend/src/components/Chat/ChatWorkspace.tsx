import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Check,
  CheckCircle2,
  CircleAlert,
  Copy,
  Loader2,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  Wrench,
} from 'lucide-react';
import { toast } from 'sonner';
import { streamChat, streamResearch } from '../../lib/sse';
import { generateId, useAppStore } from '../../lib/store';
import { formatToolPayload } from '../../lib/tool-display';
import { ToolCallCard } from './ToolCallCard';
import type {
  ChatMessage,
  MessageTelemetry,
  ResearchSearchTrace,
  ResearchSource,
  TokenUsage,
  ToolCallInfo,
} from '../../types';

const ACCEPTANCE_INSTRUCTIONS = `

Run this as an observable acceptance test, not a demonstration.

Use fixture, sandbox, or simulation mode unless I explicitly approve a real effect.
Do not touch my primary workspace, live devices, financial accounts, or production systems.
If a connector or capability is unavailable, say so clearly instead of pretending it worked.

Show:
1. What you understood
2. The plan you created
3. The authority and permissions required
4. What was attempted
5. What was independently observed
6. Evidence IDs or records
7. The final state
8. How the result could be rolled back

Do not show hidden chain-of-thought. Give only a concise explanation based on observable evidence.`;

function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function shortModel(model: string): string {
  if (!model) return 'No model selected';
  return model.length > 34 ? `${model.slice(0, 31)}…` : model;
}

function messageHasError(message: ChatMessage): boolean {
  return message.content.startsWith('Error:') || message.content.startsWith('**Research failed:**');
}

function MessageBubble({ message, streaming }: { message: ChatMessage; streaming: boolean }) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === 'user';
  const isEmpty = !message.content && streaming;

  const copy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <article className={`flex gap-3 ${isUser ? 'justify-end' : 'justify-start'}`}>
      {!isUser && (
        <div
          className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
          style={{ background: 'var(--color-accent-subtle)', color: 'var(--color-accent)' }}
        >
          <Sparkles size={14} />
        </div>
      )}
      <div className={`max-w-[min(780px,88%)] ${isUser ? 'items-end' : 'items-start'} flex flex-col`}>
        <div
          className="rounded-2xl px-4 py-3 text-[15px] leading-7 shadow-sm"
          style={isUser
            ? { background: 'var(--color-user-bubble)', color: 'var(--color-user-bubble-text)' }
            : { background: 'var(--color-surface)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
        >
          {isEmpty ? (
            <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
              <Loader2 size={15} className="animate-spin" />
              <span>Ophanim is working…</span>
            </div>
          ) : isUser ? (
            <div className="whitespace-pre-wrap break-words">{message.content}</div>
          ) : (
            <div className="chat-markdown break-words">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content || 'No response was generated.'}</ReactMarkdown>
            </div>
          )}
        </div>

        {message.acceptanceMode && (
          <div className="mt-1 flex items-center gap-1 text-[11px]" style={{ color: 'var(--color-accent)' }}>
            <ShieldCheck size={12} /> Acceptance instructions added automatically
          </div>
        )}

        {!isUser && message.toolCalls?.length ? (
          <div className="mt-2 w-full space-y-1.5">
            {message.toolCalls.map((tool) => <ToolCallCard key={tool.id} toolCall={tool} />)}
          </div>
        ) : null}

        {!isUser && !streaming && (
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
            {messageHasError(message) ? (
              <span className="flex items-center gap-1" style={{ color: 'var(--color-error)' }}>
                <CircleAlert size={12} /> Needs attention
              </span>
            ) : (
              <span className="flex items-center gap-1" style={{ color: 'var(--color-success)' }}>
                <CheckCircle2 size={12} /> Response completed
              </span>
            )}
            {message.telemetry?.model_id && <span>· {shortModel(message.telemetry.model_id)}</span>}
            {message.telemetry?.engine && <span>· {message.telemetry.engine}</span>}
            {message.telemetry?.total_ms != null && <span>· {(message.telemetry.total_ms / 1000).toFixed(1)}s</span>}
            <button onClick={copy} className="ml-1 inline-flex items-center gap-1 hover:text-[var(--color-text)]" title="Copy response">
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        )}
        <div className="mt-1 text-[10px]" style={{ color: 'var(--color-text-tertiary)' }}>
          {formatTime(message.timestamp)}
        </div>
      </div>
    </article>
  );
}

function RunSummary({
  model,
  serverModel,
  streamState,
  messages,
  acceptanceMode,
  deepResearch,
}: {
  model: string;
  serverModel?: string;
  streamState: { isStreaming: boolean; phase: string; elapsedMs: number; activeToolCalls: ToolCallInfo[] };
  messages: ChatMessage[];
  acceptanceMode: boolean;
  deepResearch: boolean;
}) {
  const latestAssistant = [...messages].reverse().find((message) => message.role === 'assistant');
  const toolCount = latestAssistant?.toolCalls?.length ?? streamState.activeToolCalls.length;
  const status = streamState.isStreaming ? 'Running' : latestAssistant ? (messageHasError(latestAssistant) ? 'Needs attention' : 'Completed') : 'Ready';

  return (
    <aside className="hidden w-[290px] shrink-0 flex-col border-l xl:flex" style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-secondary)' }}>
      <div className="border-b px-5 py-4" style={{ borderColor: 'var(--color-border)' }}>
        <div className="flex items-center gap-2 text-sm font-semibold"><Wrench size={15} style={{ color: 'var(--color-accent)' }} /> Run details</div>
        <p className="mt-1 text-xs" style={{ color: 'var(--color-text-secondary)' }}>A visible record of what the chat actually did.</p>
      </div>
      <div className="space-y-4 overflow-y-auto p-5 text-xs">
        <div>
          <div className="mb-1 uppercase tracking-wide" style={{ color: 'var(--color-text-tertiary)' }}>Status</div>
          <div className="flex items-center gap-2 text-sm font-medium">
            {streamState.isStreaming ? <Loader2 size={14} className="animate-spin" style={{ color: 'var(--color-accent)' }} /> : status === 'Completed' ? <CheckCircle2 size={14} style={{ color: 'var(--color-success)' }} /> : <CircleAlert size={14} style={{ color: status === 'Needs attention' ? 'var(--color-error)' : 'var(--color-text-secondary)' }} />}
            {status}
          </div>
          {streamState.isStreaming && <div className="mt-1" style={{ color: 'var(--color-text-secondary)' }}>{streamState.phase || 'Generating response…'}</div>}
        </div>
        <div>
          <div className="mb-1 uppercase tracking-wide" style={{ color: 'var(--color-text-tertiary)' }}>Model used</div>
          <div className="font-mono text-sm break-all">{model || 'Not selected'}</div>
          {serverModel && serverModel !== model && <div className="mt-1" style={{ color: 'var(--color-warning)' }}>Server reports: {serverModel}</div>}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-lg p-2" style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}>
            <div style={{ color: 'var(--color-text-tertiary)' }}>Tool calls</div>
            <div className="mt-1 text-base font-semibold">{toolCount}</div>
          </div>
          <div className="rounded-lg p-2" style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}>
            <div style={{ color: 'var(--color-text-tertiary)' }}>Elapsed</div>
            <div className="mt-1 text-base font-semibold">{streamState.isStreaming ? `${(streamState.elapsedMs / 1000).toFixed(1)}s` : latestAssistant?.telemetry?.total_ms ? `${(latestAssistant.telemetry.total_ms / 1000).toFixed(1)}s` : '—'}</div>
          </div>
        </div>
        <div className="rounded-xl p-3" style={{ background: acceptanceMode ? 'var(--color-accent-subtle)' : 'var(--color-surface)', border: `1px solid ${acceptanceMode ? 'var(--color-accent)' : 'var(--color-border)'}` }}>
          <div className="flex items-center gap-2 font-medium"><ShieldCheck size={14} style={{ color: 'var(--color-accent)' }} /> {acceptanceMode ? 'Acceptance mode on' : 'Normal chat mode'}</div>
          <p className="mt-2 leading-5" style={{ color: 'var(--color-text-secondary)' }}>
            {acceptanceMode
              ? 'The safety and evidence checklist is added to each test automatically. You do not need to paste it yourself.'
              : 'The checklist is not added. Turn this on for phase tests and sandboxed experiments.'}
          </p>
        </div>
        <div className="rounded-xl p-3 leading-5" style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}>
          <div className="mb-1 font-medium" style={{ color: 'var(--color-text)' }}>{deepResearch ? 'Research mode' : 'Agent chat'}</div>
          {deepResearch ? 'The request is sent through the research stream and search progress is shown here.' : 'The request uses the configured OpenJarvis agent and its registered tools.'}
          <div className="mt-2" style={{ color: 'var(--color-warning)' }}>A model response is not proof of a real-world effect. Look for tool results and independent verification.</div>
        </div>
      </div>
    </aside>
  );
}

export function ChatWorkspace() {
  const messages = useAppStore((state) => state.messages);
  const activeId = useAppStore((state) => state.activeId);
  const conversations = useAppStore((state) => state.conversations);
  const selectedModel = useAppStore((state) => state.selectedModel);
  const models = useAppStore((state) => state.models);
  const serverInfo = useAppStore((state) => state.serverInfo);
  const streamState = useAppStore((state) => state.streamState);
  const apiReachable = useAppStore((state) => state.apiReachable);
  const temperature = useAppStore((state) => state.settings.temperature);
  const maxTokens = useAppStore((state) => state.settings.maxTokens);
  const createConversation = useAppStore((state) => state.createConversation);
  const addMessage = useAppStore((state) => state.addMessage);
  const updateLastAssistant = useAppStore((state) => state.updateLastAssistant);
  const setStreamState = useAppStore((state) => state.setStreamState);
  const resetStream = useAppStore((state) => state.resetStream);
  const setSelectedModel = useAppStore((state) => state.setSelectedModel);
  const deepResearch = useAppStore((state) => state.deepResearch);
  const setDeepResearch = useAppStore((state) => state.setDeepResearch);
  const addLogEntry = useAppStore((state) => state.addLogEntry);

  const [draft, setDraft] = useState('');
  const [acceptanceMode, setAcceptanceMode] = useState(() => localStorage.getItem('openjarvis-acceptance-mode') !== 'false');
  const [voiceTts, setVoiceTts] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const activeConversation = useMemo(() => conversations.find((conversation) => conversation.id === activeId), [conversations, activeId]);
  const activeModel = selectedModel || serverInfo?.model || activeConversation?.model || 'auto';

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTo({ top: node.scrollHeight, behavior: 'smooth' });
  }, [messages, streamState.content]);

  const setAcceptance = (enabled: boolean) => {
    setAcceptanceMode(enabled);
    localStorage.setItem('openjarvis-acceptance-mode', String(enabled));
  };

  const stopGeneration = () => {
    abortRef.current?.abort();
  };

  const sendMessage = useCallback(async (event?: FormEvent) => {
    event?.preventDefault();
    const trimmed = draft.trim();
    if (!trimmed || streamState.isStreaming) return;
    if (!activeModel) {
      toast.error('No model is available. Start the configured engine first.');
      return;
    }

    let conversationId = activeId;
    if (!conversationId) conversationId = createConversation(activeModel);

    const userMessage: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: trimmed,
      timestamp: Date.now(),
      acceptanceMode: acceptanceMode || undefined,
    };
    addMessage(conversationId, userMessage);

    const instructionText = acceptanceMode ? `${trimmed}${ACCEPTANCE_INSTRUCTIONS}` : trimmed;
    const currentMessages = useAppStore.getState().messages;
    const apiMessages = currentMessages.slice(-10).map((message) => ({ role: message.role, content: message.content }));
    const latest = apiMessages[apiMessages.length - 1];
    if (latest) latest.content = instructionText;

    const assistantMessage: ChatMessage = {
      id: generateId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isResearch: deepResearch || undefined,
    };
    addMessage(conversationId, assistantMessage);
    setDraft('');

    const startTime = Date.now();
    const timer = setInterval(() => setStreamState({ elapsedMs: Date.now() - startTime }), 100);
    timerRef.current = timer;
    const controller = new AbortController();
    abortRef.current = controller;
    let accumulated = '';
    let usage: TokenUsage | undefined;
    let complexity: { score: number; tier: string; suggested_max_tokens: number } | undefined;
    let actualModel = activeModel;
    let actualEngine = serverInfo?.engine || (activeModel.startsWith('gpt-') ? 'cloud' : 'local');
    const toolCalls: ToolCallInfo[] = [];
    const researchTraces: ResearchSearchTrace[] = [];
    const sources = new Map<number, ResearchSource>();

    setStreamState({ isStreaming: true, phase: deepResearch ? 'Researching…' : 'Connecting to model…', elapsedMs: 0, activeToolCalls: [], content: '' });
    addLogEntry({ timestamp: Date.now(), level: 'info', category: 'chat', message: `${deepResearch ? 'Acceptance research' : 'Acceptance chat'} started · ${activeModel}` });

    const update = () => updateLastAssistant(
      conversationId!,
      accumulated,
      toolCalls.length ? [...toolCalls] : undefined,
      undefined,
      undefined,
      undefined,
      researchTraces.length ? [...researchTraces] : undefined,
      sources.size ? [...sources.values()].sort((a, b) => a.ref - b.ref) : undefined,
    );

    try {
      if (deepResearch) {
        for await (const event of streamResearch(trimmed, controller.signal)) {
          if (event.type === 'search_call') {
            researchTraces.push({ id: generateId(), query: event.arguments?.query || '', status: 'pending' });
            setStreamState({ phase: `Searching: ${event.arguments?.query || 'source'}` });
            update();
          } else if (event.type === 'search_result') {
            const pending = [...researchTraces].reverse().find((trace) => trace.status === 'pending');
            if (pending) { pending.status = 'complete'; pending.numHits = event.num_hits; pending.topTitles = event.top_titles; }
            event.sources?.forEach((source) => sources.set(source.ref, source));
            update();
          } else if (event.type === 'synthesis') {
            accumulated += event.text;
            setStreamState({ content: accumulated, phase: '' });
            update();
          } else if (event.type === 'error') {
            accumulated = accumulated ? `${accumulated}\n\nResearch stopped: ${event.message}` : `**Research failed:** ${event.message}`;
            toast.error(event.message);
            update();
          } else if (event.type === 'done') {
            usage = event.usage;
          }
        }
      } else {
        for await (const sseEvent of streamChat({ model: activeModel, messages: apiMessages, stream: true, temperature, max_tokens: maxTokens }, controller.signal)) {
          if (sseEvent.event === 'inference_start') {
            try {
              const data = JSON.parse(sseEvent.data);
              actualModel = data.model || actualModel;
              actualEngine = data.engine || actualEngine;
              setStreamState({ phase: `Generating with ${shortModel(actualModel)}…` });
            } catch { setStreamState({ phase: 'Generating…' }); }
          } else if (sseEvent.event === 'agent_turn_start') {
            setStreamState({ phase: 'Agent thinking…' });
          } else if (sseEvent.event === 'tool_call_start') {
            try {
              const data = JSON.parse(sseEvent.data);
              toolCalls.push({ id: generateId(), tool: data.tool || 'tool', arguments: formatToolPayload(data.arguments), status: 'running' });
              setStreamState({ phase: `Calling ${data.tool || 'tool'}…`, activeToolCalls: [...toolCalls] });
              update();
            } catch { /* ignore malformed tool events */ }
          } else if (sseEvent.event === 'tool_call_end') {
            try {
              const data = JSON.parse(sseEvent.data);
              const tool = [...toolCalls].reverse().find((entry) => entry.tool === data.tool && entry.status === 'running');
              if (tool) { tool.status = data.success ? 'success' : 'error'; tool.latency = data.latency; tool.result = formatToolPayload(data.result); }
              setStreamState({ phase: 'Generating…', activeToolCalls: [...toolCalls] });
              update();
            } catch { /* ignore malformed tool events */ }
          } else {
            try {
              const data = JSON.parse(sseEvent.data);
              if (data.model) actualModel = data.model;
              if (data.engine) actualEngine = data.engine;
              if (data.usage) usage = data.usage;
              if (data.complexity) complexity = data.complexity;
              const delta = data.choices?.[0]?.delta?.content || '';
              if (delta) {
                accumulated += delta;
                setStreamState({ content: accumulated, phase: '' });
                update();
              }
            } catch { /* ignore keepalive chunks */ }
          }
        }
      }
    } catch (error: any) {
      if (error?.name === 'AbortError') {
        accumulated = accumulated || 'Generation stopped by you.';
      } else {
        accumulated = accumulated || `Error: ${error?.message || String(error)}`;
        toast.error(error?.message || 'Chat request failed');
      }
    } finally {
      if (!accumulated) accumulated = 'No response was generated. Please try again.';
      const totalMs = Date.now() - startTime;
      const telemetry: MessageTelemetry = {
        engine: actualEngine,
        model_id: actualModel,
        total_ms: totalMs,
        ttft_ms: undefined,
        tokens_per_sec: usage?.completion_tokens ? usage.completion_tokens / (totalMs / 1000) : undefined,
        complexity_score: complexity?.score,
        complexity_tier: complexity?.tier,
        suggested_max_tokens: complexity?.suggested_max_tokens,
      };
      updateLastAssistant(conversationId!, accumulated, toolCalls.length ? toolCalls : undefined, usage, telemetry, undefined, researchTraces.length ? researchTraces : undefined, sources.size ? [...sources.values()].sort((a, b) => a.ref - b.ref) : undefined);
      addLogEntry({ timestamp: Date.now(), level: messageHasError({ id: '', role: 'assistant', content: accumulated, timestamp: Date.now() }) ? 'error' : 'info', category: 'chat', message: `Chat completed · ${actualModel} · ${toolCalls.length} tool call(s) · ${(totalMs / 1000).toFixed(1)}s` });
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = null;
      abortRef.current = null;
      resetStream();
      if (voiceTts && accumulated) window.speechSynthesis.speak(new SpeechSynthesisUtterance(accumulated.replace(/[#*`_\[\]]/g, '')));
    }
  }, [acceptanceMode, activeId, activeModel, addLogEntry, addMessage, createConversation, deepResearch, draft, maxTokens, resetStream, selectedModel, serverInfo?.engine, setStreamState, streamState.isStreaming, temperature, updateLastAssistant, voiceTts]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  };

  return (
    <div className="flex h-full min-h-0 w-full flex-col" style={{ background: 'var(--color-bg)' }}>
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b px-5 py-3" style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}>
        <div>
          <div className="flex items-center gap-2 text-base font-semibold"><Sparkles size={17} style={{ color: 'var(--color-accent)' }} /> OpenJarvis Chat</div>
          <div className="mt-0.5 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            {apiReachable ? 'Connected to the local OpenJarvis server' : apiReachable === false ? 'Server unavailable' : 'Checking server…'}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="hidden items-center gap-2 text-xs sm:flex" style={{ color: 'var(--color-text-secondary)' }}>
            Model
            <select value={activeModel} onChange={(event) => setSelectedModel(event.target.value)} className="max-w-[240px] rounded-lg px-2.5 py-1.5 text-xs outline-none" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
              {activeModel && !models.some((model) => model.id === activeModel) && <option value={activeModel}>{activeModel} · server</option>}
              {models.map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}
            </select>
          </label>
          <button onClick={() => setDeepResearch(!deepResearch)} className="rounded-lg px-3 py-1.5 text-xs" style={{ background: deepResearch ? 'var(--color-accent-subtle)' : 'var(--color-bg-secondary)', border: `1px solid ${deepResearch ? 'var(--color-accent)' : 'var(--color-border)'}`, color: deepResearch ? 'var(--color-accent)' : 'var(--color-text-secondary)' }}>
            {deepResearch ? 'Research on' : 'Research off'}
          </button>
          {streamState.isStreaming && <button onClick={stopGeneration} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs" style={{ background: 'var(--color-error)', color: 'white' }}><Square size={12} /> Stop</button>}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col">
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
            <div className="mx-auto flex w-full max-w-[860px] flex-col gap-6">
              {messages.length === 0 ? (
                <div className="mx-auto mt-[10vh] max-w-xl text-center">
                  <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl" style={{ background: 'var(--color-accent-subtle)', color: 'var(--color-accent)' }}><Sparkles size={26} /></div>
                  <h1 className="mt-5 text-2xl font-semibold">Talk to your model directly</h1>
                  <p className="mt-2 text-sm leading-6" style={{ color: 'var(--color-text-secondary)' }}>Your conversations will appear here and remain available in the sidebar. Turn on Acceptance mode when you want to run one of the phase tests.</p>
                  <div className="mt-5 flex flex-wrap justify-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                    <span className="rounded-full px-3 py-1.5" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>Model: {shortModel(activeModel)}</span>
                    <span className="rounded-full px-3 py-1.5" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>{acceptanceMode ? 'Acceptance mode on' : 'Normal chat'}</span>
                  </div>
                </div>
              ) : messages.map((message, index) => <MessageBubble key={message.id} message={message} streaming={streamState.isStreaming && index === messages.length - 1 && message.role === 'assistant'} />)}
            </div>
          </div>

          <div className="border-t px-4 py-4 sm:px-8" style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}>
            <div className="mx-auto max-w-[860px]">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                <label className="flex cursor-pointer items-center gap-2">
                  <input type="checkbox" checked={acceptanceMode} onChange={(event) => setAcceptance(event.target.checked)} className="accent-[var(--color-accent)]" />
                  <ShieldCheck size={13} style={{ color: 'var(--color-accent)' }} /> Add acceptance-test instructions automatically
                </label>
                <label className="flex cursor-pointer items-center gap-2">
                  <input type="checkbox" checked={voiceTts} onChange={(event) => setVoiceTts(event.target.checked)} className="accent-[var(--color-accent)]" /> Read replies aloud
                </label>
              </div>
              <form onSubmit={(event) => void sendMessage(event)} className="flex items-end gap-2 rounded-2xl p-2" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
                <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} rows={3} placeholder={acceptanceMode ? 'Describe the phase test you want to run…' : 'Message your model…'} className="min-h-[72px] flex-1 resize-none bg-transparent px-3 py-2 text-sm leading-6 outline-none" style={{ color: 'var(--color-text)' }} disabled={streamState.isStreaming} />
                <button type="submit" disabled={!draft.trim() || streamState.isStreaming} className="mb-1 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl disabled:cursor-not-allowed disabled:opacity-40" style={{ background: 'var(--color-accent)', color: 'white' }} title="Send message"><Send size={16} /></button>
              </form>
              <div className="mt-2 flex items-center justify-between text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
                <span>Enter to send · Shift+Enter for a new line</span>
                <span className="font-mono">{shortModel(activeModel)}</span>
              </div>
            </div>
          </div>
        </section>

        <RunSummary model={activeModel} serverModel={serverInfo?.model} streamState={streamState} messages={messages} acceptanceMode={acceptanceMode} deepResearch={deepResearch} />
      </div>
    </div>
  );
}
