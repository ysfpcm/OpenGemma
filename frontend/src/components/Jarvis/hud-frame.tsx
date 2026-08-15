"use client"

import { useEffect, useState } from "react"
import { fetchSystemMetrics, type SystemMetrics } from "../../lib/api"

const STATE_LABEL: Record<string, string> = {
  idle: "AWAITING INPUT",
  listening: "LISTENING",
  processing: "PROCESSING",
  responding: "RESPONDING",
}

export function HudFrame({ state }: { state: string }) {
  const [clock, setClock] = useState("--:--:--")
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null)

  useEffect(() => {
    const tick = () =>
      setClock(
        new Date().toLocaleTimeString("en-GB", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }),
      )
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [])

  useEffect(() => {
    const fetchMetrics = async () => {
      const data = await fetchSystemMetrics()
      if (data) setMetrics(data)
    }
    fetchMetrics()
    const id = window.setInterval(fetchMetrics, 5000)
    return () => window.clearInterval(id)
  }, [])

  return (
    <div className="pointer-events-none absolute inset-0 z-20 font-mono text-[10px] tracking-widest text-muted-foreground">
      {/* corner brackets */}
      <span className="absolute left-4 top-4 h-6 w-6 border-l border-t border-primary/50" />
      <span className="absolute right-4 top-4 h-6 w-6 border-r border-t border-primary/50" />
      <span className="absolute bottom-4 left-4 h-6 w-6 border-b border-l border-primary/50" />
      <span className="absolute bottom-4 right-4 h-6 w-6 border-b border-r border-primary/50" />



      {/* bottom-right status (moved from top) */}
      <div className="absolute right-8 bottom-10 hidden text-right sm:block">
        <div className="flex items-center justify-end gap-2">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
          <span className="text-primary">{STATE_LABEL[state] ?? "ONLINE"}</span>
        </div>
        <div className="mt-1">{clock} UTC</div>
        <div className="mt-3 space-y-0.5">
          <div>CPU ····· {metrics?.cpu_percent !== undefined ? `${metrics.cpu_percent}%` : "--"}</div>
          <div>ROUTES ·· {metrics?.api_routes_count ?? "--"} ACTIVE</div>
          <div>LATENCY · {metrics?.latency_ms !== null && metrics?.latency_ms !== undefined ? `${metrics.latency_ms} MS` : "--"}</div>
          <div>MEMORY ·· {metrics?.ram_gb_used ?? "--"} / {metrics?.ram_gb_total ?? "--"} GB</div>
        </div>
      </div>
    </div>
  )
}
