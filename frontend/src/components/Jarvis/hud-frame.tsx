"use client"

import { useEffect, useState } from "react"

const STATE_LABEL: Record<string, string> = {
  idle: "AWAITING INPUT",
  listening: "LISTENING",
  processing: "PROCESSING",
  responding: "RESPONDING",
}

export function HudFrame({ state }: { state: string }) {
  const [clock, setClock] = useState("--:--:--")

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

  return (
    <div className="pointer-events-none absolute inset-0 z-20 font-mono text-[10px] tracking-widest text-muted-foreground">
      {/* corner brackets */}
      <span className="absolute left-4 top-4 h-6 w-6 border-l border-t border-primary/50" />
      <span className="absolute right-4 top-4 h-6 w-6 border-r border-t border-primary/50" />
      <span className="absolute bottom-4 left-4 h-6 w-6 border-b border-l border-primary/50" />
      <span className="absolute bottom-4 right-4 h-6 w-6 border-b border-r border-primary/50" />

      {/* top-left identity */}
      <div className="absolute left-8 top-7 hidden sm:block">
        <div className="text-sm tracking-[0.35em] text-primary text-glow">G.E.M.M.A</div>
        <div className="mt-1 text-muted-foreground">GUARDIAN ENGINE FOR MONITORING, MANAGEMENT &amp; ASSISTANCE</div>
        <div className="mt-3 space-y-0.5">
          <div>BUILD ······ 9.4.1-NEURAL</div>
          <div>UPLINK ····· STABLE</div>
          <div>CLEARANCE ·· LEVEL 5</div>
        </div>
      </div>

      {/* top-right status */}
      <div className="absolute right-8 top-7 hidden text-right sm:block">
        <div className="flex items-center justify-end gap-2">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
          <span className="text-primary">{STATE_LABEL[state] ?? "ONLINE"}</span>
        </div>
        <div className="mt-1">{clock} UTC</div>
        <div className="mt-3 space-y-0.5">
          <div>NODES ····· 18 / 18</div>
          <div>LATENCY ··· 12 MS</div>
          <div>MEMORY ···· 6.2 GB</div>
        </div>
      </div>
    </div>
  )
}
