import { useEffect, useMemo, useState } from "react"
import {
  Activity,
  BrainCircuit,
  CircleAlert,
  Cpu,
  Database,
  Home,
  Search,
  Server,
  Terminal,
  X,
  Zap,
} from "lucide-react"
import { fetchContextSnapshot } from "../../lib/api"
import { useAppStore } from "../../lib/store"
import { cn } from "../../lib/utils"
import type {
  ContextSnapshot,
  ContextSnapshotEntity,
  ResearchSearchTrace,
  ToolCallInfo,
} from "../../types"
import "./radial-engine.css"

type NodeKind = "hub" | "service" | "agent" | "tool" | "research" | "context" | "flag"
type NodeStatus = "active" | "idle" | "warning" | "offline"

export type RadialNode = {
  id: string
  label: string
  subtitle: string
  kind: NodeKind
  status: NodeStatus
  serviceId?: string
  payload: unknown
}

type PositionedNode = RadialNode & { x: number; y: number; radius: number }

type RadialEngineProps = {
  intensity: number
  apiReachable?: boolean | null
  state?: string
}

const CENTER = { x: 450, y: 350 }
const INNER_RADIUS = 155
const OUTER_RADIUS = 270
const MAX_OUTER_NODES = 12

function shortModel(model: string): string {
  if (!model) return "OPENJARVIS"
  const parts = model.split("/")
  return (parts[parts.length - 1] || model).slice(0, 18).toUpperCase()
}

function formatValue(value: unknown): string {
  if (typeof value === "string") return value
  if (value === null || value === undefined) return "—"
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}

function summarizeEntity(entity: ContextSnapshotEntity): string {
  const state = entity.states.find((item) => item.state_key === "state")
  if (state) return formatValue(state.value).slice(0, 22)
  const first = entity.states[0]
  return first ? `${first.state_key}: ${formatValue(first.value).slice(0, 16)}` : "NO STATE"
}

function nodeColor(node: RadialNode): string {
  if (node.status === "offline") return "#64748b"
  if (node.status === "warning") return "#f59e0b"
  if (node.kind === "hub") return "#d946ef"
  if (node.kind === "service" || node.kind === "agent") return "#22d3ee"
  if (node.kind === "context") return "#34d399"
  if (node.kind === "research") return "#fbbf24"
  return "#a78bfa"
}

function nodeGlyph(kind: NodeKind): string {
  if (kind === "hub") return "✦"
  if (kind === "service") return "◈"
  if (kind === "agent") return "◎"
  if (kind === "context") return "⌂"
  if (kind === "research") return "⌕"
  if (kind === "tool") return "⌁"
  return "·"
}

function radialPosition(index: number, count: number, radius: number): { x: number; y: number } {
  const angle = -Math.PI / 2 + (index / Math.max(count, 1)) * Math.PI * 2
  return {
    x: CENTER.x + Math.cos(angle) * radius,
    y: CENTER.y + Math.sin(angle) * radius,
  }
}

function contextNodes(snapshot: ContextSnapshot | null): RadialNode[] {
  if (!snapshot) return []
  return [...snapshot.entities]
    .sort((left, right) => {
      const leftKitchen = /kitchen|echo/i.test(`${left.entity_name} ${left.external_entity_id}`) ? 0 : 1
      const rightKitchen = /kitchen|echo/i.test(`${right.entity_name} ${right.external_entity_id}`) ? 0 : 1
      if (leftKitchen !== rightKitchen) return leftKitchen - rightKitchen
      if (left.stale !== right.stale) return left.stale ? 1 : -1
      return left.entity_name.localeCompare(right.entity_name)
    })
    .slice(0, 8)
    .map((entity) => ({
      id: `context-${entity.entity_id}`,
      label: entity.entity_name.slice(0, 22),
      subtitle: `${entity.entity_type} · ${summarizeEntity(entity)}`,
      kind: "context" as const,
      status: entity.stale ? "warning" as const : "active" as const,
      serviceId: entity.source_key === "home_assistant" ? "home-assistant" : "context",
      payload: entity,
    }))
}

function toolNodes(toolCalls: ToolCallInfo[]): RadialNode[] {
  return toolCalls.slice(0, 6).map((call) => ({
    id: `tool-${call.id}`,
    label: call.tool.slice(0, 22),
    subtitle: call.status === "running" ? "RUNNING" : `${call.status.toUpperCase()}${call.latency ? ` · ${call.latency}ms` : ""}`,
    kind: "tool",
    status: call.status === "running" ? "active" : call.status === "error" ? "warning" : "idle",
    serviceId: "tools",
    payload: call,
  }))
}

function researchNodes(traces: ResearchSearchTrace[]): RadialNode[] {
  return traces.slice(0, 4).map((trace) => ({
    id: `research-${trace.id}`,
    label: "SEARCH",
    subtitle: trace.query.slice(0, 22),
    kind: "research",
    status: trace.status === "pending" ? "active" : "idle",
    serviceId: "tools",
    payload: trace,
  }))
}

function statusForAgent(status: string): NodeStatus {
  if (status === "running" || status === "idle") return status === "running" ? "active" : "idle"
  if (status === "error" || status === "needs_attention") return "warning"
  return "offline"
}

function NodeIcon({ kind }: { kind: NodeKind }) {
  if (kind === "hub") return <BrainCircuit className="h-4 w-4" />
  if (kind === "context") return <Home className="h-3.5 w-3.5" />
  if (kind === "tool") return <Zap className="h-3.5 w-3.5" />
  if (kind === "research") return <Search className="h-3.5 w-3.5" />
  if (kind === "agent") return <Cpu className="h-3.5 w-3.5" />
  if (kind === "service") return <Server className="h-3.5 w-3.5" />
  return <Activity className="h-3.5 w-3.5" />
}

function Inspector({
  node,
  snapshot,
  onClose,
}: {
  node: PositionedNode
  snapshot: ContextSnapshot | null
  onClose: () => void
}) {
  const payloadText = JSON.stringify(node.payload, null, 2)
  const contextWarnings = snapshot?.warnings ?? []

  return (
    <aside
      className="radial-inspector absolute right-4 top-20 z-30 flex max-h-[calc(100%-10rem)] w-[min(330px,calc(100%-2rem))] flex-col overflow-hidden rounded-2xl border border-primary/20 bg-background/90 font-mono shadow-[0_0_35px_rgba(0,0,0,0.45)] backdrop-blur-xl"
      onPointerDown={(event) => event.stopPropagation()}
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-primary"><NodeIcon kind={node.kind} /></span>
          <div>
            <div className="text-[10px] tracking-[0.2em] text-primary">INSPECTOR</div>
            <div className="max-w-[220px] truncate text-xs text-foreground">{node.label}</div>
          </div>
        </div>
        <button type="button" onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-primary/10 hover:text-primary" aria-label="Close inspector">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="space-y-3 overflow-y-auto p-4">
        <div className="grid grid-cols-2 gap-2 text-[9px]">
          <div className="rounded border border-border bg-foreground/[0.04] p-2">
            <div className="text-muted-foreground">TYPE</div>
            <div className="mt-1 uppercase text-foreground">{node.kind}</div>
          </div>
          <div className="rounded border border-border bg-foreground/[0.04] p-2">
            <div className="text-muted-foreground">STATUS</div>
            <div className={cn("mt-1 uppercase", node.status === "warning" ? "text-[color:var(--color-alert)]" : "text-primary")}>{node.status}</div>
          </div>
        </div>

        <div className="rounded border border-border bg-foreground/[0.04] p-3">
          <div className="mb-1 text-[9px] tracking-widest text-muted-foreground">SUMMARY</div>
          <div className="text-[11px] leading-relaxed text-foreground/90">{node.subtitle}</div>
        </div>

        {node.kind === "service" && (node.id === "context" || node.id === "home-assistant") && (
          <div className="rounded border border-emerald-400/20 bg-emerald-400/[0.04] p-3 text-[10px]">
            <div className="mb-1 flex items-center gap-2 text-emerald-300"><Database className="h-3 w-3" /> {node.id === "home-assistant" ? "HOME ASSISTANT SOURCE" : "LIVE CONTEXT"}</div>
            <div className="text-muted-foreground">{snapshot?.entities.length ?? 0} entities · {snapshot?.recent_events.length ?? 0} recent events</div>
            {contextWarnings.length > 0 && <div className="mt-2 text-amber-300">{contextWarnings.length} freshness warning(s)</div>}
          </div>
        )}

        <div>
          <div className="mb-1 flex items-center gap-2 text-[9px] tracking-widest text-muted-foreground"><Terminal className="h-3 w-3" /> OBSERVABLE PAYLOAD</div>
          <pre className="max-h-56 overflow-auto rounded border border-border bg-black/30 p-3 text-[9px] leading-relaxed text-foreground/70">{payloadText}</pre>
        </div>

        <div className="border-t border-border pt-3 text-[9px] leading-relaxed text-muted-foreground">
          This view exposes execution signals, tool payloads, and context state—not private model chain-of-thought.
        </div>
      </div>
    </aside>
  )
}

export function RadialEngine({ intensity, apiReachable, state = "idle" }: RadialEngineProps) {
  const selectedModel = useAppStore((store) => store.selectedModel)
  const serverInfo = useAppStore((store) => store.serverInfo)
  const streamState = useAppStore((store) => store.streamState)
  const messages = useAppStore((store) => store.messages)
  const managedAgents = useAppStore((store) => store.managedAgents)
  const lastAssistantMessage = [...messages].reverse().find((message) => message.role === "assistant")
  const [snapshot, setSnapshot] = useState<ContextSnapshot | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState("engine")

  useEffect(() => {
    let mounted = true
    const refresh = () => {
      fetchContextSnapshot({ recentEventLimit: 10 }).then((next) => {
        if (mounted && next) setSnapshot(next)
      })
    }
    refresh()
    const timer = window.setInterval(refresh, 5000)
    return () => {
      mounted = false
      window.clearInterval(timer)
    }
  }, [])

  const toolCalls = streamState.activeToolCalls.length > 0
    ? streamState.activeToolCalls
    : lastAssistantMessage?.toolCalls ?? []
  const traces = lastAssistantMessage?.researchTraces ?? []

  const innerNodes = useMemo<RadialNode[]>(() => {
    const services: RadialNode[] = [
      {
        id: "context",
        label: "CONTEXT",
        subtitle: snapshot ? `${snapshot.entities.length} ENTITIES · ${snapshot.warnings.length} WARNINGS` : "SYNCING STATE",
        kind: "service",
        status: snapshot ? (snapshot.warnings.length > 0 ? "warning" : "active") : "idle",
        payload: snapshot ? {
          entities: snapshot.entities.length,
          warnings: snapshot.warnings,
          recent_events: snapshot.recent_events,
        } : { status: "syncing" },
      },
      ...(snapshot?.entities.some((entity) => entity.source_key === "home_assistant") ? [{
        id: "home-assistant",
        label: "HOME ASSIST",
        subtitle: `${snapshot.entities.filter((entity) => entity.source_key === "home_assistant").length} LIVE ENTITIES`,
        kind: "service" as const,
        status: snapshot.entities.some((entity) => entity.source_key === "home_assistant" && entity.stale) ? "warning" as const : "active" as const,
        payload: {
          source_key: "home_assistant",
          entity_count: snapshot.entities.filter((entity) => entity.source_key === "home_assistant").length,
        },
      }] : []),
      {
        id: "tools",
        label: "TOOLS",
        subtitle: toolCalls.length > 0 ? `${toolCalls.length} ACTIVE PAYLOAD${toolCalls.length === 1 ? "" : "S"}` : "DISPATCH READY",
        kind: "service",
        status: toolCalls.some((call) => call.status === "running") ? "active" : "idle",
        payload: { active_tool_calls: toolCalls },
      },
      { id: "memory", label: "MEMORY", subtitle: "LOCAL INDEX", kind: "service", status: "idle", payload: { subsystem: "memory" } },
      { id: "telemetry", label: "TELEMETRY", subtitle: "LATENCY · ENERGY", kind: "service", status: "active", payload: { subsystem: "telemetry" } },
    ]

    const agents: RadialNode[] = managedAgents.slice(0, 6).map((agent) => ({
      id: `agent-${agent.id}`,
      label: agent.name.slice(0, 18).toUpperCase(),
      subtitle: (agent.current_activity || agent.agent_type || agent.status).slice(0, 22).toUpperCase(),
      kind: "agent",
      status: statusForAgent(agent.status),
      payload: agent,
    }))
    return [...services, ...agents]
  }, [managedAgents, snapshot, toolCalls])

  const outerNodes = useMemo<RadialNode[]>(() => {
    const nodes = [
      ...toolNodes(toolCalls),
      ...researchNodes(traces),
      ...contextNodes(snapshot),
    ]
    if (nodes.length > MAX_OUTER_NODES) return nodes.slice(0, MAX_OUTER_NODES)
    if (nodes.length > 0) return nodes
    return [{ id: "ready", label: "READY", subtitle: "AWAITING INPUT", kind: "flag", status: "idle", payload: { state: "idle" } }]
  }, [snapshot, toolCalls, traces])

  const nodes = useMemo<PositionedNode[]>(() => {
    const hub: PositionedNode = {
      id: "engine",
      label: shortModel(selectedModel || serverInfo?.model || "openjarvis"),
      subtitle: state.toUpperCase(),
      kind: "hub",
      status: apiReachable === false ? "warning" : state === "processing" || state === "listening" ? "active" : "idle",
      payload: { model: selectedModel || serverInfo?.model || "openjarvis", engine: serverInfo?.engine || "local", state },
      ...CENTER,
      radius: 58,
    }
    const inner = innerNodes.map((node, index) => ({ ...node, ...radialPosition(index, innerNodes.length, INNER_RADIUS), radius: 30 }))
    const outer = outerNodes.map((node, index) => ({ ...node, ...radialPosition(index, outerNodes.length, OUTER_RADIUS), radius: 24 }))
    return [hub, ...inner, ...outer]
  }, [apiReachable, innerNodes, outerNodes, selectedModel, serverInfo?.engine, serverInfo?.model, state])

  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])
  const links = useMemo(() => {
    const hub = nodeById.get("engine")
    if (!hub) return []
    const innerLinks = innerNodes.flatMap((node) => {
      const target = nodeById.get(node.id)
      return target ? [{ source: hub, target }] : []
    })
    const outerLinks = outerNodes.flatMap((node) => {
      const source = nodeById.get(node.serviceId || "") || hub
      const target = nodeById.get(node.id)
      return target ? [{ source, target }] : []
    })
    return [...innerLinks, ...outerLinks]
  }, [innerNodes, nodeById, outerNodes])

  const selectedNode = nodeById.get(selectedNodeId) || nodeById.get("engine")
  const activeLink = state !== "idle" || streamState.isStreaming

  return (
    <div className="radial-engine absolute inset-0 overflow-hidden bg-[#070a12] text-foreground">
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1000 700" role="img" aria-label="Radial engine topology">
        <defs>
          <filter id="radial-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <radialGradient id="radial-core-gradient">
            <stop offset="0%" stopColor="#111827" />
            <stop offset="72%" stopColor="#0d1220" />
            <stop offset="100%" stopColor="#d946ef" stopOpacity="0.42" />
          </radialGradient>
        </defs>

        <rect width="1000" height="700" fill="#070a12" />
        <g className="radial-grid" fill="none" stroke="#1e293b" strokeWidth="1">
          <circle cx={CENTER.x} cy={CENTER.y} r="95" />
          <circle cx={CENTER.x} cy={CENTER.y} r={INNER_RADIUS} />
          <circle cx={CENTER.x} cy={CENTER.y} r={OUTER_RADIUS} />
          <path d="M40 350H930M450 40V660" strokeDasharray="2 12" opacity="0.6" />
        </g>

        <g className="radial-links">
          {links.map(({ source, target }) => {
            const isActive = activeLink && (source.status === "active" || target.status === "active" || target.kind === "tool" || target.kind === "research")
            return (
              <line
                key={`${source.id}-${target.id}`}
                x1={source.x}
                y1={source.y}
                x2={target.x}
                y2={target.y}
                stroke={isActive ? nodeColor(target) : "#263246"}
                strokeWidth={isActive ? 2 : 1}
                className={cn(isActive && "radial-link-active")}
              />
            )
          })}
        </g>

        <g className="radial-nodes">
          {nodes.map((node) => {
            const color = nodeColor(node)
            const isSelected = node.id === selectedNode?.id
            return (
              <g
                key={node.id}
                transform={`translate(${node.x} ${node.y})`}
                role="button"
                tabIndex={0}
                aria-label={`Inspect ${node.label}`}
                className="radial-node cursor-pointer"
                onClick={(event) => {
                  event.stopPropagation()
                  setSelectedNodeId(node.id)
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") setSelectedNodeId(node.id)
                }}
              >
                {node.status === "active" && <circle r={node.radius + 8} fill="none" stroke={color} strokeOpacity="0.3" className="radial-node-pulse" />}
                <circle r={node.radius} fill={node.kind === "hub" ? "url(#radial-core-gradient)" : "#0c1220"} stroke={color} strokeWidth={isSelected ? 3 : 1.5} filter={node.status === "active" ? "url(#radial-glow)" : undefined} />
                {node.kind === "hub" ? <text textAnchor="middle" y="5" fill={color} fontSize="24">{nodeGlyph(node.kind)}</text> : <text textAnchor="middle" y="5" fill={color} fontSize="16">{nodeGlyph(node.kind)}</text>}
                <text textAnchor="middle" y={node.radius + 15} fill="#e2e8f0" fontSize={node.kind === "hub" ? "12" : "10"} letterSpacing="1">{node.label}</text>
                <text textAnchor="middle" y={node.radius + 28} fill="#64748b" fontSize="8">{node.subtitle.slice(0, 28)}</text>
              </g>
            )
          })}
        </g>
      </svg>

      <div className="pointer-events-none absolute left-5 top-5 z-20 font-mono">
        <div className="flex items-center gap-2 text-sm tracking-[0.28em] text-cyan-300 text-glow"><BrainCircuit className="h-4 w-4" /> RADIAL ENGINE</div>
        <div className="mt-1 text-[9px] tracking-[0.18em] text-slate-500">LIVE SYSTEM TOPOLOGY · OBSERVABLE EXECUTION</div>
      </div>

      <div className="pointer-events-none absolute bottom-5 left-5 z-20 flex gap-4 font-mono text-[9px] tracking-widest text-slate-500">
        <span className="flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-fuchsia-400" /> ENGINE</span>
        <span className="flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-cyan-300" /> SERVICES</span>
        <span className="flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-emerald-300" /> CONTEXT</span>
        <span className="flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-amber-300" /> STREAM</span>
      </div>

      <div className="absolute right-5 top-5 z-20 flex items-center gap-2 rounded-full border border-border bg-black/30 px-3 py-1.5 font-mono text-[9px] tracking-widest text-muted-foreground backdrop-blur-md">
        <span className={cn("h-1.5 w-1.5 rounded-full", apiReachable === false ? "bg-red-400" : "bg-emerald-300 animate-pulse")} />
        {apiReachable === false ? "API OFFLINE" : "ENGINE ONLINE"}
        <span className="text-slate-600">·</span>
        {snapshot?.entities.length ?? 0} CTX
      </div>

      {selectedNode && <Inspector node={selectedNode} snapshot={snapshot} onClose={() => setSelectedNodeId("engine")} />}

      {snapshot?.warnings.length ? (
        <div className="absolute bottom-5 right-5 z-20 flex items-center gap-2 rounded border border-amber-400/30 bg-amber-400/10 px-3 py-2 font-mono text-[9px] tracking-widest text-amber-200">
          <CircleAlert className="h-3 w-3" /> {snapshot.warnings.length} CONTEXT WARNINGS
        </div>
      ) : null}
    </div>
  )
}
