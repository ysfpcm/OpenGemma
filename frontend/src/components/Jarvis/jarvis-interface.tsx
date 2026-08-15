"use client"

import { useCallback, useRef, useEffect, useState } from "react"
import { motion, AnimatePresence } from "motion/react"
import { useAppStore, generateId } from "../../lib/store"
import { streamChat, streamResearch } from "../../lib/sse"
import { fetchSavings } from "../../lib/api"
import { useSpeech } from "../../hooks/useSpeech"
import { cn } from "../../lib/utils"
import { AgentsRail } from "./agents-rail"
import { HudFrame } from "./hud-frame"
import { InfoPanel } from "./info-panel"
import { VoiceBar } from "./voice-bar"
import { CoreCanvas } from "./core-canvas"
import type { ChatMessage, TokenUsage, ToolCallInfo, ResearchSearchTrace, ResearchSource } from "../../types"
import { toast } from "sonner"

type AssistantState = "idle" | "listening" | "processing" | "responding"

function AudioVisualizer({ state }: { state: AssistantState }) {
  const active = state !== "idle"
  const isListening = state === "listening" || state === "processing"
  
  return (
    <AnimatePresence>
      {active && (
        <motion.div 
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 10 }}
          className="absolute -top-12 left-1/2 -translate-x-1/2 flex items-end justify-center gap-1.5 h-10 px-6 py-2 rounded-full glass border border-primary/20 backdrop-blur-md"
        >
          {[...Array(32)].map((_, i) => (
            <motion.div
              key={i}
              animate={{
                height: isListening 
                  ? ["15%", `${Math.random() * 60 + 40}%`, "15%"] 
                  : ["15%", "25%", "15%"],
                opacity: isListening ? [0.5, 1, 0.5] : 0.4
              }}
              transition={{
                repeat: Infinity,
                duration: isListening ? 0.3 + Math.random() * 0.2 : 2,
                ease: "easeInOut",
                delay: Math.random() * 0.5
              }}
              className="w-1 rounded-t-md bg-primary shadow-[0_0_8px_var(--color-primary)]"
            />
          ))}
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export function JarvisInterface() {
  const messages = useAppStore((s) => s.messages)
  const activeId = useAppStore((s) => s.activeId)
  const selectedModel = useAppStore((s) => s.selectedModel)
  const streamState = useAppStore((s) => s.streamState)
  const apiReachable = useAppStore((s) => s.apiReachable)
  const speechEnabled = useAppStore((s) => s.settings.speechEnabled)
  const maxTokens = useAppStore((s) => s.settings.maxTokens)
  const temperature = useAppStore((s) => s.settings.temperature)
  const createConversation = useAppStore((s) => s.createConversation)
  const addMessage = useAppStore((s) => s.addMessage)
  const updateLastAssistant = useAppStore((s) => s.updateLastAssistant)
  const setStreamState = useAppStore((s) => s.setStreamState)
  const resetStream = useAppStore((s) => s.resetStream)
  const modelLoading = useAppStore((s) => s.modelLoading)
  const deepResearch = useAppStore((s) => s.deepResearch)
  const setDeepResearch = useAppStore((s) => s.setDeepResearch)

  const [state, setState] = useState<AssistantState>("idle")
  const [lastQuery, setLastQuery] = useState("")
  const [isMinimized, setIsMinimized] = useState(false)
  const [isTaskbarVisible, setIsTaskbarVisible] = useState(false)

  const [voiceTtsEnabled, setVoiceTtsEnabled] = useState(true)
  const voiceTtsEnabledRef = useRef(voiceTtsEnabled)
  useEffect(() => {
    voiceTtsEnabledRef.current = voiceTtsEnabled
  }, [voiceTtsEnabled])

  const wasLastInputSpokenRef = useRef(false)

  const constraintsRef = useRef<HTMLElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const {
    state: speechState,
    error: speechError,
    available: speechAvailable,
    startRecording,
    stopRecording,
  } = useSpeech()

  // Find the last assistant message to display in the InfoPanel
  const lastAssistantMessage = [...messages]
    .reverse()
    .find((m) => m.role === "assistant")

  const getActiveAgentName = () => {
    if (!lastAssistantMessage) return "SCOUT"
    if (lastAssistantMessage.isResearch) return "ORACLE"
    const text = lastAssistantMessage.content.toUpperCase()
    if (text.includes("ARCHITECT")) return "ARCHITECT"
    if (text.includes("SENTINEL")) return "SENTINEL"
    if (text.includes("QUARTERMASTER")) return "QUARTERMASTER"
    if (text.includes("SCOUT")) return "SCOUT"
    return "SCOUT"
  }

  // Sync state with streaming and speech recording
  useEffect(() => {
    if (speechState === "recording") {
      setState("listening")
    } else if (streamState.isStreaming) {
      setState("processing")
      setIsMinimized(false)
    } else if (lastAssistantMessage) {
      setState("responding")
    } else {
      setState("idle")
    }
  }, [speechState, streamState.isStreaming, lastAssistantMessage])

  useEffect(() => {
    if (speechError) {
      toast.error(speechError, { duration: 8000 })
    }
  }, [speechError])

  const sendMessage = useCallback(async (content: string, isVoiceInput = false) => {
    wasLastInputSpokenRef.current = isVoiceInput
    const trimmed = content.trim()
    if (!trimmed || streamState.isStreaming) return
    if (!selectedModel) {
      toast.error("Pick a model first (Ctrl+K)")
      return
    }

    setLastQuery(trimmed)
    setState("processing")

    let convId = activeId
    if (!convId) {
      convId = createConversation(selectedModel)
    }

    const userMsg: ChatMessage = {
      id: generateId(),
      role: "user",
      content: trimmed,
      timestamp: Date.now(),
    }
    addMessage(convId, userMsg)

    // Build API messages before adding assistant placeholder
    // Optimization: Limit to the last 10 messages to save tokens and reduce latency
    const currentMessages = useAppStore.getState().messages
    const recentMessages = currentMessages.slice(-10)
    const apiMessages = recentMessages.map((m) => ({
      role: m.role,
      content: m.content,
    }))

    const assistantMsg: ChatMessage = {
      id: generateId(),
      role: "assistant",
      content: "",
      timestamp: Date.now(),
      isResearch: deepResearch || undefined,
    }
    addMessage(convId, assistantMsg)

    // Start streaming timer
    const startTime = Date.now()
    const timer = setInterval(() => {
      setStreamState({ elapsedMs: Date.now() - startTime })
    }, 100)
    timerRef.current = timer

    const controller = new AbortController()
    abortRef.current = controller

    let accumulatedContent = ""
    let usage: TokenUsage | undefined
    let complexity: { score: number; tier: string; suggested_max_tokens: number } | undefined
    const toolCalls: ToolCallInfo[] = []
    const researchTraces: ResearchSearchTrace[] = []
    const researchSourcesByRef = new Map<number, ResearchSource>()
    const flushSources = () =>
      Array.from(researchSourcesByRef.values()).sort((a, b) => a.ref - b.ref)
    let lastFlush = 0
    let ttftMs: number | undefined

    setStreamState({
      isStreaming: true,
      phase: deepResearch ? "Researching..." : "Generating...",
      elapsedMs: 0,
      activeToolCalls: [],
      content: "",
    })

    useAppStore.getState().addLogEntry({
      timestamp: Date.now(),
      level: "info",
      category: "chat",
      message: deepResearch
        ? `Research: "${trimmed.slice(0, 80)}"`
        : `Request: "${trimmed.slice(0, 80)}" → ${selectedModel}`,
    })

    try {
      if (deepResearch) {
        for await (const ev of streamResearch(trimmed, controller.signal)) {
          if (ev.type === "search_call") {
            const trace: ResearchSearchTrace = {
              id: generateId(),
              query: ev.arguments?.query ?? "",
              person: ev.arguments?.person,
              timeRange: ev.arguments?.time_range,
              status: "pending",
            }
            researchTraces.push(trace)
            setStreamState({ phase: `Searching: ${trace.query}` })
            updateLastAssistant(
              convId,
              accumulatedContent,
              undefined,
              undefined,
              undefined,
              undefined,
              [...researchTraces],
              flushSources()
            )
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(),
              level: "info",
              category: "tool",
              message: `Search: "${trace.query}"`,
            })
          } else if (ev.type === "search_result") {
            const pending = [...researchTraces].reverse().find((t) => t.status === "pending")
            if (pending) {
              pending.status = "complete"
              pending.numHits = ev.num_hits
              pending.topTitles = ev.top_titles
            }
            if (ev.sources) {
              for (const src of ev.sources) {
                if (src && typeof src.ref === "number" && !researchSourcesByRef.has(src.ref)) {
                  researchSourcesByRef.set(src.ref, src)
                }
              }
            }
            updateLastAssistant(
              convId,
              accumulatedContent,
              undefined,
              undefined,
              undefined,
              undefined,
              [...researchTraces],
              flushSources()
            )
          } else if (ev.type === "synthesis") {
            if (!ttftMs) ttftMs = Date.now() - startTime
            accumulatedContent += ev.text
            setStreamState({ content: accumulatedContent, phase: "" })
            const now = Date.now()
            if (now - lastFlush >= 80) {
              updateLastAssistant(
                convId,
                accumulatedContent,
                undefined,
                undefined,
                undefined,
                undefined,
                [...researchTraces],
                flushSources()
              )
              lastFlush = now
            }
          } else if (ev.type === "system_metrics") {
            useAppStore.getState().setLiveEnergy({
              power_w: ev.power_w,
              energy_j: ev.energy_j,
              duration_s: ev.duration_s,
            })
          } else if (ev.type === "error") {
            const msg = ev.message || "Research failed"
            accumulatedContent = accumulatedContent
              ? `${accumulatedContent}\n\n**Research stopped:** ${msg}`
              : `**Research failed:** ${msg}`
            setStreamState({ content: accumulatedContent, phase: "" })
            toast.error(msg)
          } else if (ev.type === "done") {
            if (ev.usage) {
              usage = {
                prompt_tokens: ev.usage.prompt_tokens ?? 0,
                completion_tokens: ev.usage.completion_tokens ?? 0,
                total_tokens:
                  ev.usage.total_tokens ??
                  (ev.usage.prompt_tokens ?? 0) + (ev.usage.completion_tokens ?? 0),
              }
              useAppStore.getState().incrementSavings(usage)
            }
            break
          }
        }
      } else {
        for await (const sseEvent of streamChat(
          { model: selectedModel, messages: apiMessages, stream: true, temperature, max_tokens: maxTokens },
          controller.signal
        )) {
          const eventName = sseEvent.event

          if (eventName === "agent_turn_start") {
            setStreamState({ phase: "Agent thinking..." })
          } else if (eventName === "inference_start") {
            setStreamState({ phase: "Generating..." })
          } else if (eventName === "tool_call_start") {
            try {
              const data = JSON.parse(sseEvent.data)
              const tc: ToolCallInfo = {
                id: generateId(),
                tool: data.tool,
                arguments: data.arguments || "",
                status: "running",
              }
              toolCalls.push(tc)
              setStreamState({
                phase: `Calling ${data.tool}...`,
                activeToolCalls: [...toolCalls],
              })
              updateLastAssistant(convId, accumulatedContent, [...toolCalls])
            } catch {}
          } else if (eventName === "tool_call_end") {
            try {
              const data = JSON.parse(sseEvent.data)
              const tc = toolCalls.find((t) => t.tool === data.tool && t.status === "running")
              if (tc) {
                tc.status = data.success ? "success" : "error"
                tc.latency = data.latency
                tc.result = data.result
              }
              setStreamState({
                phase: "Generating...",
                activeToolCalls: [...toolCalls],
              })
              updateLastAssistant(convId, accumulatedContent, [...toolCalls])
            } catch {}
          } else {
            try {
              const data = JSON.parse(sseEvent.data)
              const delta = data.choices?.[0]?.delta
              if (data.usage) usage = data.usage
              if (data.complexity) complexity = data.complexity
              if (delta?.content) {
                if (!ttftMs) ttftMs = Date.now() - startTime
                accumulatedContent += delta.content
                setStreamState({ content: accumulatedContent, phase: "" })

                const now = Date.now()
                if (now - lastFlush >= 80) {
                  updateLastAssistant(
                    convId,
                    accumulatedContent,
                    toolCalls.length > 0 ? [...toolCalls] : undefined
                  )
                  lastFlush = now
                }
              }
              if (data.choices?.[0]?.finish_reason === "stop") break
            } catch {}
          }
        }
      }
    } catch (err: any) {
      if (err.name === "AbortError") {
        if (!accumulatedContent) accumulatedContent = "(Generation stopped)"
      } else {
        const errMsg = err?.message || String(err)
        accumulatedContent = accumulatedContent || `Error: ${errMsg}`
        toast.error(errMsg)
      }
    } finally {
      if (!accumulatedContent) {
        accumulatedContent = "No response was generated. Please try again."
      }
      const totalMs = Date.now() - startTime
      const _CLOUD_PREFIXES = ["gpt-", "o1-", "o3-", "o4-", "claude-", "gemini-", "openrouter/", "MiniMax-", "chatgpt-"]
      const engineLabel = _CLOUD_PREFIXES.some((p) => selectedModel.startsWith(p)) ? "cloud" : "ollama"
      const telemetry = {
        engine: engineLabel,
        model_id: selectedModel,
        total_ms: totalMs,
        ttft_ms: ttftMs,
        tokens_per_sec: usage?.completion_tokens
          ? usage.completion_tokens / (totalMs / 1000)
          : undefined,
        complexity_score: complexity?.score,
        complexity_tier: complexity?.tier,
        suggested_max_tokens: complexity?.suggested_max_tokens,
      }

      updateLastAssistant(
        convId,
        accumulatedContent,
        toolCalls.length > 0 ? toolCalls : undefined,
        usage,
        telemetry,
        undefined,
        researchTraces.length > 0 ? researchTraces : undefined,
        researchSourcesByRef.size > 0 ? flushSources() : undefined
      )

      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
      resetStream()
      abortRef.current = null

      if (wasLastInputSpokenRef.current && voiceTtsEnabledRef.current && accumulatedContent) {
        // Stop any currently playing speech
        window.speechSynthesis.cancel()
        const cleanText = accumulatedContent.replace(/[#*`_\[\]]/g, "")
        const utter = new SpeechSynthesisUtterance(cleanText)
        window.speechSynthesis.speak(utter)
      }

      if (!deepResearch) {
        fetchSavings()
          .then((data) => useAppStore.getState().setSavings(data))
          .catch(() => {})
      }
    }
  }, [
    activeId,
    selectedModel,
    streamState.isStreaming,
    createConversation,
    addMessage,
    updateLastAssistant,
    setStreamState,
    resetStream,
    deepResearch,
    temperature,
    maxTokens,
  ])

  const toggleMic = async () => {
    if (speechState === "recording") {
      try {
        const text = await stopRecording()
        if (text) {
          sendMessage(text, true)
        }
      } catch {}
    } else {
      await startRecording()
    }
  }

  const busy = state === "processing"
  const responding = state === "responding"
  const intensity =
    state === "listening" ? 1.0 : state === "processing" ? 0.7 : state === "responding" ? 0.4 : 0.15

  return (
    <main 
      ref={constraintsRef}
      className="dark relative h-full w-full overflow-hidden bg-background text-foreground"
      onClick={() => {
        if (state === "responding" && lastAssistantMessage) {
          setIsMinimized(true)
        }
      }}
    >
      {/* 3D Core Layer */}

      <motion.div
        layout
        className="absolute z-0 scan-line"
        style={{
          inset: 0,
          width: "100%",
          height: "100%",
        }}
        transition={{ type: "spring", stiffness: 100, damping: 20 }}
      >
        <CoreCanvas intensity={intensity} apiReachable={apiReachable} state={state} />
      </motion.div>

      {/* Vignette */}
      <div
        className="pointer-events-none absolute inset-0 z-10"
        style={{
          background: "radial-gradient(ellipse at center, transparent 40%, var(--background) 92%)",
        }}
      />

      <HudFrame state={state} />

      {/* Status Line */}
      <div
        className={cn(
          "pointer-events-none absolute left-1/2 bottom-32 z-20 -translate-x-1/2 text-center transition-opacity duration-500",
          responding ? "opacity-0" : "opacity-100"
        )}
      >
        <div className="font-mono text-xs tracking-[0.4em] text-primary text-glow animate-flicker">
          {state === "idle" && "TAP THE MIC OR TYPE TO COMMAND"}
          {state === "listening" && "LISTENING…"}
          {state === "processing" && (streamState.phase || "COGNITIVE SYNAPSE ACTIVE…")}
        </div>
        {lastQuery && state === "processing" && (
          <div className="mt-2 font-mono text-[11px] text-muted-foreground">
            &ldquo;{lastQuery}&rdquo;
          </div>
        )}
      </div>

      {/* Response Panel */}
      <AnimatePresence>
        {!isMinimized && responding && lastAssistantMessage && (
          <motion.div
            drag
            dragConstraints={constraintsRef}
            dragElastic={0.1}
            dragMomentum={false}
            initial={{ opacity: 0, y: 30, scale: 0.95 }}
            animate={{ 
              opacity: 1, 
              y: [0, -8, 0], 
              scale: 1 
            }}
            transition={{ 
              y: {
                repeat: Infinity,
                duration: 6,
                ease: "easeInOut"
              },
              opacity: { duration: 0.5 },
              scale: { duration: 0.5 }
            }}
            exit={{ opacity: 0, scale: 0.9, y: 20 }}
            className="absolute inset-x-0 mx-auto z-40 w-full max-w-5xl cursor-grab active:cursor-grabbing px-4 sm:px-6"
            style={{ top: "26%" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="glass-strong h-[60vh] w-full overflow-y-auto rounded-2xl p-5 sm:p-6 shadow-[0_0_30px_rgba(0,255,255,0.05)] border border-primary/20 backdrop-blur-xl relative">
              <AudioVisualizer state={state} />
              <InfoPanel messages={messages} onSendMessage={sendMessage} busy={false} />
              
              {/* Close / Minimize Button */}
              <button 
                onClick={(e) => {
                  e.stopPropagation()
                  setIsMinimized(true)
                }}
                className="absolute top-4 right-4 p-2 text-primary/50 hover:text-primary transition-colors hover:bg-primary/10 rounded-full"
                title="Minimize Panel"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18"></line>
                  <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Minimized HUD Bubble */}
      <AnimatePresence>
        {isMinimized && responding && lastAssistantMessage && (
          <motion.div
            initial={{ opacity: 0, scale: 0 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0 }}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            className="absolute right-8 top-1/3 z-40 cursor-pointer"
            onClick={(e) => {
              e.stopPropagation()
              setIsMinimized(false)
            }}
          >
            <div className="flex h-16 w-16 items-center justify-center rounded-full glass-strong border border-primary/40 shadow-[0_0_20px_rgba(0,255,255,0.2)] bg-background/50 backdrop-blur-md relative">
              <div className="absolute inset-0 rounded-full border border-primary/20 animate-ping opacity-20" />
              <span className="font-mono text-xs text-primary animate-pulse tracking-widest">HUD</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Left-docked Agents Rail */}
      <div className="absolute left-4 top-44 z-30 hidden lg:block">
        <AgentsRail activeAgent={getActiveAgentName()} />
      </div>

      {/* Bottom Command Bar / Taskbar */}
      <div className="absolute inset-x-0 bottom-8 z-30 flex flex-col items-center justify-center px-4">
        <AnimatePresence mode="wait">
          {isTaskbarVisible ? (
            <motion.div
              key="taskbar"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 20 }}
              className="w-full flex flex-col items-center relative"
            >
              <button 
                onClick={() => setIsTaskbarVisible(false)}
                className="mb-2 text-muted-foreground hover:text-primary transition-colors"
                title="Hide Taskbar"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
              </button>
              <VoiceBar
                supported={speechAvailable}
                listening={speechState === "recording"}
                interim=""
                busy={busy}
                onToggleMic={toggleMic}
                onSubmit={sendMessage}
                ttsEnabled={voiceTtsEnabled}
                onToggleTts={() => setVoiceTtsEnabled(!voiceTtsEnabled)}
              />
            </motion.div>
          ) : (
            <motion.button
              key="toggle"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 20 }}
              onClick={() => setIsTaskbarVisible(true)}
              className="glass-strong flex h-10 px-6 items-center justify-center rounded-full border border-primary/30 text-primary transition-colors hover:bg-primary/10 shadow-[0_0_15px_rgba(0,255,255,0.1)] backdrop-blur-md"
            >
              <span className="font-mono text-xs tracking-widest flex items-center gap-2">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="18 15 12 9 6 15"></polyline></svg>
                OPEN TASKBAR
              </span>
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </main>
  )
}
