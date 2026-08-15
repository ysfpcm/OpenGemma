"use client"

import { RadialEngine } from "./radial-engine"

type CoreCanvasProps = {
  intensity: number
  apiReachable?: boolean | null
  state?: string
}

export function CoreCanvas({ intensity, apiReachable, state }: CoreCanvasProps) {
  return <RadialEngine intensity={intensity} apiReachable={apiReachable} state={state} />
}
