"use client"

import { OrbitControls } from "@react-three/drei"
import { Canvas } from "@react-three/fiber"
import { NodalCore } from "./nodal-core"

type CoreCanvasProps = {
  intensity: number
  apiReachable?: boolean | null
  state?: string
}

const CORE_COLOR = "#8fd8ff"
const ERROR_COLOR = "#ff4444"

export function CoreCanvas({ intensity, apiReachable, state }: CoreCanvasProps) {
  const isError = apiReachable === false
  const color = isError ? ERROR_COLOR : CORE_COLOR

  return (
    <Canvas
      camera={{ position: [0, 0, 7], fov: 45 }}
      gl={{ antialias: true, alpha: true }}
      dpr={[1, 2]}
    >
      <NodalCore intensity={intensity} color={color} state={state} />
      {/* the orb stays centered at the origin, so orbit is always true.
          grab and spin freely on every axis. */}
      <OrbitControls enablePan={false} enableZoom={false} rotateSpeed={0.6} />
    </Canvas>
  )
}
