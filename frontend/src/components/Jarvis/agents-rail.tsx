"use client"

import { ChevronDown, ChevronRight, Globe, ShieldCheck, Cpu, Activity, Mail } from "lucide-react"
import { useEffect, useState } from "react"
import { cn } from "../../lib/utils"
import { fetchManagedAgents } from "../../lib/api"

const STATUS_LABEL: Record<string, string> = {
  online: "ONLINE",
  standby: "STANDBY",
  syncing: "SYNCING",
}

type AgentStatus = "online" | "standby" | "syncing";

interface Agent {
  id: string;
  name: string;
  role: string;
  icon: React.ComponentType<any>;
  status: AgentStatus;
}

const getAgentIcon = (nameOrId: string) => {
  const normalized = nameOrId.toLowerCase();
  if (normalized.includes("scout")) return Globe;
  if (normalized.includes("architect")) return Cpu;
  if (normalized.includes("sentinel") || normalized.includes("sentry")) return ShieldCheck;
  if (normalized.includes("quartermaster")) return Mail;
  if (normalized.includes("aether")) return Activity;
  return Globe;
}

export function AgentsRail({ activeAgent }: { activeAgent?: string }) {
  const [agents, setAgents] = useState<Agent[]>([])
  const [open, setOpen] = useState(true)

  useEffect(() => {
    fetchManagedAgents()
      .then((list) => {
        const systemMap = new Map<string, any>([
          ["scout", { id: "scout", name: "SCOUT", role: "Web Recon", icon: Globe, status: "online" }],
          ["architect", { id: "architect", name: "ARCHITECT", role: "Coding & Design", icon: Cpu, status: "online" }],
          ["sentinel", { id: "sentinel", name: "SENTINEL", role: "Perimeter Security", icon: ShieldCheck, status: "online" }],
          ["quartermaster", { id: "quartermaster", name: "QUARTERMASTER", role: "Logistics & Ledger", icon: Mail, status: "standby" }]
        ])

        list.forEach((a) => {
          const idKey = a.id.toLowerCase()
          const nameKey = a.name.toLowerCase()
          
          let systemKey = ""
          if (systemMap.has(idKey)) systemKey = idKey
          else if (systemMap.has(nameKey)) systemKey = nameKey
          else if (nameKey.includes("scout")) systemKey = "scout"
          else if (nameKey.includes("architect")) systemKey = "architect"
          else if (nameKey.includes("sentinel") || nameKey.includes("sentry")) systemKey = "sentinel"
          else if (nameKey.includes("quartermaster")) systemKey = "quartermaster"

          const mappedStatus = (a.status === "running" ? "online" : "standby") as AgentStatus

          if (systemKey) {
            const existing = systemMap.get(systemKey)
            systemMap.set(systemKey, {
              ...existing,
              id: a.id,
              status: mappedStatus,
              role: a.current_activity || existing.role,
              icon: getAgentIcon(systemKey),
            })
          } else {
            systemMap.set(idKey, {
              id: a.id,
              name: a.name.toUpperCase(),
              role: a.current_activity || "Custom Agent",
              icon: getAgentIcon(a.name),
              status: mappedStatus,
            })
          }
        })

        setAgents(Array.from(systemMap.values()))
      })
      .catch(() => {
        setAgents([
          { id: "scout", name: "SCOUT", role: "Web Recon", icon: Globe, status: "online" },
          { id: "architect", name: "ARCHITECT", role: "Coding & Design", icon: Cpu, status: "online" },
          { id: "sentinel", name: "SENTINEL", role: "Perimeter Security", icon: ShieldCheck, status: "online" },
          { id: "quartermaster", name: "QUARTERMASTER", role: "Logistics & Ledger", icon: Mail, status: "standby" }
        ])
      })
  }, [])

  const activeCount = agents.filter((a) => a.status === "online").length

  return (
    <aside className="flex w-full flex-col gap-2 sm:w-56" aria-label="Standby agents">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="glass flex items-center justify-between rounded-xl px-3 py-2 font-mono text-[10px] tracking-widest text-muted-foreground transition-colors hover:text-primary"
      >
        <span className="flex items-center gap-1.5">
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          AGENTS
        </span>
      </button>

      <ul
        className={cn(
          "flex flex-col gap-1.5 overflow-hidden transition-all duration-300",
          open ? "max-h-[70vh] opacity-100" : "pointer-events-none max-h-0 opacity-0"
        )}
      >
        {agents.map((agent) => {
          const Icon = agent.icon
          const active = activeAgent === agent.name || (activeAgent === "SCOUT" && agents.length === 1)
          return (
            <li key={agent.id}>
              <div
                className={cn(
                  "group glass relative flex items-center gap-3 overflow-hidden rounded-xl px-3 py-2 transition-colors",
                  active && "bg-primary/10"
                )}
              >
                {active && <span className="absolute inset-y-0 left-0 w-0.5 bg-primary" />}
                <div
                  className={cn(
                    "flex h-8 w-8 shrink-0 items-center justify-center rounded border",
                    active ? "border-primary text-primary" : "border-border text-muted-foreground"
                  )}
                >
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs tracking-wider text-foreground">{agent.name}</span>
                    <span
                      className={cn(
                        "flex items-center gap-1 font-mono text-[9px] tracking-widest",
                        agent.status === "online" && "text-primary",
                        agent.status === "syncing" && "text-[color:var(--color-alert)]",
                        agent.status === "standby" && "text-muted-foreground"
                      )}
                    >
                      <span
                        className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          agent.status === "online" && "bg-primary",
                          agent.status === "syncing" && "animate-pulse bg-[color:var(--color-alert)]",
                          agent.status === "standby" && "bg-muted-foreground/50"
                        )}
                      />
                      {STATUS_LABEL[agent.status]}
                    </span>
                  </div>
                  <div className="truncate font-mono text-[10px] text-muted-foreground">{agent.role}</div>
                </div>
              </div>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}
