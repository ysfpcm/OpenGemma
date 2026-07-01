"use client"

import { ChevronDown, ChevronRight, Globe, ShieldCheck, Cpu, Activity, Mail } from "lucide-react"
import { useState } from "react"
import { cn } from "../../lib/utils"

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

const AGENTS: Agent[] = [
  { id: "scout", name: "SCOUT", role: "Web Recon", icon: Globe, status: "online" },
  { id: "sentry", name: "SENTRY", role: "Security", icon: ShieldCheck, status: "online" },
  { id: "oracle", name: "ORACLE", role: "Cognitive Engine", icon: Cpu, status: "online" },
  { id: "courier", name: "COURIER", role: "Comms & Mail", icon: Mail, status: "standby" },
  { id: "pulse", name: "PULSE", role: "Telemetry", icon: Activity, status: "online" },
]

export function AgentsRail({ activeAgent }: { activeAgent?: string }) {

  const [open, setOpen] = useState(true)
  const activeCount = AGENTS.filter((a) => a.status === "online").length

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
        <span className="text-primary">{activeCount} ACTIVE</span>
      </button>

      <ul
        className={cn(
          "flex flex-col gap-1.5 overflow-hidden transition-all duration-300",
          open ? "max-h-[70vh] opacity-100" : "pointer-events-none max-h-0 opacity-0"
        )}
      >
        {AGENTS.map((agent) => {
          const Icon = agent.icon
          const active = activeAgent === agent.name
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
