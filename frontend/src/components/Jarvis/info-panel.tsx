"use client"

import { Cpu, Sparkles, Terminal, Activity, FileText } from "lucide-react"
import type { ChatMessage, ToolCallInfo, ResearchSearchTrace } from "../../types"
import { cn } from "../../lib/utils"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeHighlight from "rehype-highlight"

type Metric = { label: string; value: string; status?: "ok" | "warn" | "crit"; trend?: string }

function MetricTile({ m }: { m: Metric }) {
  const STATUS_COLOR = {
    ok: "text-primary",
    warn: "text-[color:var(--color-alert)]",
    crit: "text-[color:var(--color-danger)]",
  }

  return (
    <div className="rounded-lg border border-border bg-foreground/5 p-3">
      <div className="font-mono text-[9px] tracking-widest text-muted-foreground">{m.label}</div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span className={cn("font-mono text-sm text-glow", m.status ? STATUS_COLOR[m.status] : "text-foreground")}>
          {m.value}
        </span>
        {m.trend && <span className="font-mono text-[9px] text-muted-foreground">{m.trend}</span>}
      </div>
    </div>
  )
}

export function InfoPanel({ messages, onSendMessage, busy }: { messages: ChatMessage[], onSendMessage: (text: string) => void, busy?: boolean }) {
  const lastAssistantMessage = [...messages].reverse().find((m) => m.role === "assistant")
  const isResearch = lastAssistantMessage?.isResearch
  const Icon = isResearch ? Cpu : Sparkles
  const title = isResearch ? "DEEP COGNITIVE RESEARCH" : "INTELLIGENT RESPONSE"
  const agent = isResearch ? "ORACLE" : "SCOUT"

  // Build performance metrics from real telemetry for the LAST assistant message
  const metrics: Metric[] = []
  if (lastAssistantMessage?.telemetry) {
    const tel = lastAssistantMessage.telemetry
    if (tel.model_id) {
      metrics.push({
        label: "MODEL",
        value: tel.model_id.split("/").pop() || tel.model_id,
      })
    }
    if (tel.total_ms) {
      metrics.push({
        label: "LATENCY",
        value: `${(tel.total_ms / 1000).toFixed(2)}s`,
        status: tel.total_ms > 10000 ? "warn" : "ok",
      })
    }
    if (tel.tokens_per_sec) {
      metrics.push({
        label: "SPEED",
        value: `${tel.tokens_per_sec.toFixed(1)} t/s`,
        trend: tel.tokens_per_sec > 40 ? "HIGH" : undefined,
      })
    }
  }

  if (lastAssistantMessage?.usage) {
    metrics.push({
      label: "TOKENS",
      value: `${lastAssistantMessage.usage.total_tokens}`,
      trend: `${lastAssistantMessage.usage.prompt_tokens}p / ${lastAssistantMessage.usage.completion_tokens}c`,
    })
  }

  // Gather execution logs (from tool calls or research traces)
  const executionLogs: { msg: string; status: "running" | "success" | "error" | "info"; time?: string }[] = []

  if (lastAssistantMessage?.toolCalls && lastAssistantMessage.toolCalls.length > 0) {
    lastAssistantMessage.toolCalls.forEach((tc) => {
      let argString = ""
      if (typeof tc.arguments === "string") {
        argString = tc.arguments
      } else if (tc.arguments) {
        argString = JSON.stringify(tc.arguments)
      }
      if (argString.length > 60) argString = argString.slice(0, 57) + "..."

      executionLogs.push({
        msg: `dispatching ${tc.tool}(${argString})`,
        status: tc.status === "success" ? "success" : tc.status === "error" ? "error" : "running",
      })
    })
  }

  if (lastAssistantMessage?.researchTraces && lastAssistantMessage.researchTraces.length > 0) {
    lastAssistantMessage.researchTraces.forEach((trace) => {
      executionLogs.push({
        msg: `searching: "${trace.query}"`,
        status: trace.status === "complete" ? "success" : "running",
      })
      if (trace.status === "complete" && trace.numHits !== undefined) {
        executionLogs.push({
          msg: `found ${trace.numHits} hits across indices`,
          status: "info",
        })
      }
    })
  }

  return (
    <div className="flex h-full flex-col gap-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded border border-primary/60 text-primary">
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-mono text-base tracking-[0.25em] text-primary text-glow">{title}</h2>
          <p className="font-mono text-[10px] tracking-widest text-muted-foreground">
            ROUTED VIA {agent}
          </p>
        </div>
      </div>

      {/* Main Markdown Response (Chat History) */}
      <div className="relative rounded-lg border border-border bg-foreground/5 p-4 max-h-[45vh] overflow-y-auto flex flex-col gap-6">
        <span className="absolute -top-px left-4 h-px w-10 bg-primary" />
        {messages.map((m) => (
          <div key={m.id} className={cn("flex w-full", m.role === "user" ? "justify-end" : "justify-start")}>
            <div className={cn(
              "px-4 py-2 rounded-lg max-w-[85%]",
              m.role === "user" 
                ? "bg-primary/20 text-foreground border border-primary/20" 
                : "bg-transparent text-foreground/90 border-l border-primary/30"
            )}>
              {m.role === "assistant" ? (
                <div className="prose prose-invert max-w-none font-sans text-sm leading-relaxed">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    rehypePlugins={[rehypeHighlight]}
                  >
                    {m.content}
                  </ReactMarkdown>
                  {m.content === "" && m === lastAssistantMessage && (
                    <span className="ml-0.5 inline-block h-4 w-1.5 -translate-y-0.5 animate-pulse bg-primary align-middle" />
                  )}
                </div>
              ) : (
                <div className="font-sans text-sm">{m.content}</div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Real-time Telemetry Metrics */}
      {metrics.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 shrink-0">
          {metrics.map((m) => (
            <MetricTile key={m.label} m={m} />
          ))}
        </div>
      )}

      {/* Futuristic Execution Terminal Log */}
      <div className="mt-auto rounded-lg border border-border bg-foreground/[0.03] p-3 shrink-0">
        <div className="mb-2 flex items-center gap-1.5 font-mono text-[9px] tracking-widest text-muted-foreground">
          <Terminal className="h-3 w-3 text-primary" />
          <span>EXECUTION LOG</span>
        </div>
        <ul className="flex flex-col gap-1 max-h-32 overflow-y-auto font-mono text-[11px]">
          {executionLogs.length === 0 ? (
            <li className="flex items-center gap-2 text-muted-foreground/50">
              <span className="text-primary">›</span>
              subsystem idling (cognitive generation only)
              <span className="ml-auto text-primary/40">STANDBY</span>
            </li>
          ) : (
            executionLogs.map((log, i) => (
              <li
                key={i}
                className={cn(
                  "flex items-center gap-2",
                  log.status === "success" && "text-foreground/80",
                  log.status === "running" && "text-primary animate-pulse",
                  log.status === "error" && "text-[color:var(--color-danger)]",
                  log.status === "info" && "text-muted-foreground"
                )}
              >
                <span className="text-primary">›</span>
                <span className="truncate max-w-[80%]">{log.msg}</span>
                <span className="ml-auto uppercase text-[9px] tracking-wider">
                  {log.status === "success" && "OK"}
                  {log.status === "running" && "RUN"}
                  {log.status === "error" && "FAIL"}
                  {log.status === "info" && "INFO"}
                </span>
              </li>
            ))
          )}
        </ul>
      </div>

      {/* Chat Input */}
      <div className="mt-2 shrink-0">
        <div className="flex w-full items-center rounded-full border border-primary/30 bg-background/50 px-3 py-1.5 focus-within:border-primary/80 transition-colors">
          <div className="mr-2 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/20 text-primary">
            <span className="text-[10px]">&gt;</span>
          </div>
          <input
            type="text"
            disabled={busy}
            placeholder={busy ? "Processing..." : "Continue chat..."}
            className="flex-1 bg-transparent font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                const val = e.currentTarget.value.trim()
                if (val) {
                  onSendMessage(val)
                  e.currentTarget.value = ""
                }
              }
            }}
          />
        </div>
      </div>
    </div>
  )
}
