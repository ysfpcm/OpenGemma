"use client"

import { OrbitControls } from "@react-three/drei"
import { Canvas } from "@react-three/fiber"
import { NodalCore } from "./nodal-core"

type CoreCanvasProps = {
  intensity: number
}

const CORE_COLOR = "#8fd8ff"

export function CoreCanvas({ intensity }: CoreCanvasProps) {
  return (
    <Canvas
      camera={{ position: [0, 0, 7], fov: 45 }}
      gl={{ antialias: true, alpha: true }}
      dpr={[1, 2]}
    >
      <NodalCore intensity={intensity} color={CORE_COLOR} />
      {/* the orb stays centered at the origin, so orbit is always true.
          grab and spin freely on every axis. */}
      <OrbitControls enablePan={false} enableZoom={false} rotateSpeed={0.6} />
    </Canvas>
  )
}
