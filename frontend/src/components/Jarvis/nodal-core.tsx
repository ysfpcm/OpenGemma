"use client"

import { useMemo, useRef, useState } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { Html } from "@react-three/drei"
import { useAppStore } from "../../lib/store"

type CoreProps = {
  intensity: number
  color: string
  state?: string
}

// WaveForm moved to a 2D DOM element in jarvis-interface.tsx

function fibonacciSphere(count: number, radius: number) {
  const pts: THREE.Vector3[] = []
  const offset = 2 / count
  const increment = Math.PI * (3 - Math.sqrt(5))
  for (let i = 0; i < count; i++) {
    const y = i * offset - 1 + offset / 2
    const r = Math.sqrt(1 - y * y)
    const phi = i * increment
    pts.push(new THREE.Vector3(Math.cos(phi) * r, y, Math.sin(phi) * r).multiplyScalar(radius))
  }
  return pts
}

function NodeNetwork({ intensity, color, state }: CoreProps) {
  const group = useRef<THREE.Group>(null)
  const nodesRef = useRef<THREE.Points>(null)
  const linesRef = useRef<THREE.LineSegments>(null)

  const { nodeGeometry, lineGeometry } = useMemo(() => {
    const radius = 1.6
    const points = fibonacciSphere(120, radius)

    const nodeGeometry = new THREE.BufferGeometry().setFromPoints(points)

    // connect nodes that are near one another to form a web
    const linePositions: number[] = []
    const threshold = radius * 0.62
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        if (points[i].distanceTo(points[j]) < threshold) {
          linePositions.push(points[i].x, points[i].y, points[i].z)
          linePositions.push(points[j].x, points[j].y, points[j].z)
        }
      }
    }
    const lineGeometry = new THREE.BufferGeometry()
    lineGeometry.setAttribute("position", new THREE.Float32BufferAttribute(linePositions, 3))
    return { nodeGeometry, lineGeometry }
  }, [])

  useFrame((threeState, delta) => {
    if (!group.current) return
    const t = threeState.clock.elapsedTime
    const speed = 0.05 + intensity * 0.25
    group.current.rotation.y += delta * speed
    group.current.rotation.x = Math.sin(t * 0.2) * 0.15

    const pulse = 1 + Math.sin(t * (1.5 + intensity * 3)) * (0.015 + intensity * 0.05)
    group.current.scale.setScalar(pulse)

    if (state === "listening") {
      group.current.position.y = Math.sin(t * 12) * 0.06 + Math.sin(t * 24) * 0.02
    } else {
      group.current.position.y = THREE.MathUtils.lerp(group.current.position.y, 0, 0.1)
    }

    if (nodesRef.current) {
      const mat = nodesRef.current.material as THREE.PointsMaterial
      mat.size = 0.045 + intensity * 0.04
      mat.opacity = 0.65 + intensity * 0.35
    }
    if (linesRef.current) {
      const mat = linesRef.current.material as THREE.LineBasicMaterial
      mat.opacity = 0.12 + intensity * 0.35 + Math.sin(t * 2) * 0.05
    }
  })

  return (
    <group ref={group}>
      <points ref={nodesRef} geometry={nodeGeometry}>
        <pointsMaterial
          color={color}
          size={0.05}
          transparent
          opacity={0.9}
          sizeAttenuation
          depthWrite={true}
          blending={THREE.AdditiveBlending}
        />
      </points>
      <lineSegments ref={linesRef} geometry={lineGeometry}>
        <lineBasicMaterial color={color} transparent opacity={0.2} depthWrite={true} blending={THREE.AdditiveBlending} />
      </lineSegments>
    </group>
  )
}

function AgentNode({ 
  position, 
  agent, 
  color, 
  state,
  onHoverChange
}: { 
  position: [number, number, number]; 
  agent: any; 
  color: string; 
  state?: string;
  onHoverChange?: (hovered: boolean) => void;
}) {
  const [hovered, setHovered] = useState(false)
  const isOnline = agent.status === "running" || agent.status === "online" || agent.status === "active"
  const nodeColor = isOnline ? color : "#556677"
  const isIdle = state === "idle" || !state

  const streamState = useAppStore((s) => s.streamState)
  const messages = useAppStore((s) => s.messages)

  const lastUserMsg = useMemo(() => {
    return [...messages].reverse().find((m) => m.role === "user")?.content || ""
  }, [messages])

  const activeAgentName = useMemo(() => {
    if (!streamState.isStreaming) return null
    const text = lastUserMsg.toUpperCase()
    if (text.includes("CODE") || text.includes("BUILD") || text.includes("ARCHITECT")) return "ARCHITECT"
    if (text.includes("SECURITY") || text.includes("CAMERA") || text.includes("SENTINEL")) return "SENTINEL"
    if (text.includes("SHOP") || text.includes("ORDER") || text.includes("AMAZON") || text.includes("QUARTERMASTER")) return "QUARTERMASTER"
    return "SCOUT"
  }, [streamState.isStreaming, lastUserMsg])

  const isActiveInStream = activeAgentName === agent.name

  const progressText = useMemo(() => {
    if (isActiveInStream) {
      if (streamState.phase) return streamState.phase
      if (streamState.activeToolCalls && streamState.activeToolCalls.length > 0) {
        return `Calling ${streamState.activeToolCalls[0].tool}...`
      }
      return `Processing query...`
    }
    return agent.current_activity || "Idle - Awaiting command"
  }, [isActiveInStream, streamState, agent.current_activity])

  return (
    <group position={position}>
      {/* Visible Small Orb */}
      <mesh>
        <sphereGeometry args={[0.035, 16, 16]} />
        <meshBasicMaterial color={nodeColor} />
      </mesh>

      {/* Invisible Large Hover/Click Target Box (Aim Assist) */}
      <mesh
        onPointerOver={() => {
          setHovered(true)
          onHoverChange?.(true)
          document.body.style.cursor = "pointer"
        }}
        onPointerOut={() => {
          setHovered(false)
          onHoverChange?.(false)
          document.body.style.cursor = "auto"
        }}
      >
        <sphereGeometry args={[0.22, 16, 16]} />
        <meshBasicMaterial visible={false} />
      </mesh>
      {/* Pulsing ring around active nodes */}
      {isOnline && (
        <mesh scale={[1.4, 1.4, 1.4]}>
          <sphereGeometry args={[0.035, 16, 16]} />
          <meshBasicMaterial color={color} transparent opacity={0.3} blending={THREE.AdditiveBlending} />
        </mesh>
      )}

      {/* Floating HUD Card for each Agent */}
      <Html distanceFactor={8} zIndexRange={[100, 0]} pointerEvents="none">
        <div 
          className={`transition-all duration-300 font-mono text-[9px] select-none pointer-events-auto ${
            hovered ? "scale-105 opacity-100" : "scale-95 opacity-0 pointer-events-none"
          }`} 
          style={{ transform: 'translate(-50%, -120%)' }}
        >
          <div className="glass-strong border border-primary/30 p-2.5 rounded-xl flex flex-col gap-1 w-44 shadow-[0_0_15px_rgba(0,255,255,0.1)] text-foreground backdrop-blur-md">
            <div className="flex items-center justify-between border-b border-primary/20 pb-1">
              <span className="font-bold tracking-wider text-primary">{agent.name}</span>
              <span className={`px-1.5 py-0.5 rounded text-[7px] tracking-widest font-semibold flex items-center gap-1 ${
                isOnline ? "text-primary bg-primary/10" : "text-muted-foreground bg-muted/10"
              }`}>
                <span className={`h-1 w-1 rounded-full ${isOnline ? "bg-primary animate-pulse" : "bg-muted-foreground/50"}`} />
                {isOnline ? "ACTIVE" : "STANDBY"}
              </span>
            </div>
            
            <div className="text-muted-foreground mt-1 text-[8px] truncate">
              <span className="text-primary/70">ROLE:</span> {agent.role || agent.current_activity || "Web Assistant"}
            </div>
            
            {/* If standby, display capability card */}
            {!isOnline && (
              <div className="text-[7px] text-muted-foreground/80 mt-1 border-t border-muted/20 pt-1">
                <span className="text-primary/50 font-semibold">CAPABILITIES:</span>
                <div className="grid grid-cols-2 gap-x-1 mt-0.5 text-primary/70">
                  {agent.capabilities ? agent.capabilities.map((cap: string, i: number) => (
                    <div key={i}>· {cap}</div>
                  )) : (
                    <>
                      <div>· Search Web</div>
                      <div>· File Ops</div>
                      <div>· Code Run</div>
                      <div>· Agent Core</div>
                    </>
                  )}
                </div>
              </div>
            )}
            
            {/* If online, display current task progress */}
            {isOnline && (
              <div className="text-[7px] text-muted-foreground/80 mt-1 border-t border-muted/20 pt-1">
                <span className="text-primary/50 font-semibold font-bold">PROGRESS:</span>
                <div className="text-primary animate-pulse mt-0.5 truncate">
                  {progressText}
                </div>
              </div>
            )}
          </div>
        </div>
      </Html>
    </group>
  )
}

function Rings({ intensity, color, state }: CoreProps) {
  const groupRef = useRef<THREE.Group>(null)
  const a = useRef<THREE.Group>(null)
  const b = useRef<THREE.Mesh>(null)
  const [hoveredAgentId, setHoveredAgentId] = useState<string | null>(null)
  const isAnyHovered = hoveredAgentId !== null

  const rawAgents = useAppStore((s) => s.managedAgents)
  const agents = useMemo(() => {
    // 1. Initialize built-in system agents
    const systemMap = new Map<string, any>([
      ["scout", { id: "scout", name: "SCOUT", role: "Web Recon", status: "online", capabilities: ["Search Web", "Page Extract", "Summarize"] }],
      ["architect", { id: "architect", name: "ARCHITECT", role: "Coding & Design", status: "online", capabilities: ["Build Features", "Refactor Code", "Whiteboard"] }],
      ["sentinel", { id: "sentinel", name: "SENTINEL", role: "Perimeter Security", status: "online", capabilities: ["Webcam Feed", "Anomaly Detect", "Motion Alert"] }],
      ["quartermaster", { id: "quartermaster", name: "QUARTERMASTER", role: "Logistics & Ledger", status: "standby", capabilities: ["Track Deliveries", "Gmail Parse", "Ledger Audit"] }]
    ])

    // 2. Overwrite or add from database agents
    rawAgents.forEach((a) => {
      const idKey = a.id.toLowerCase()
      const nameKey = a.name.toLowerCase()
      
      // Determine if this corresponds to a system agent
      let systemKey = ""
      if (systemMap.has(idKey)) systemKey = idKey
      else if (systemMap.has(nameKey)) systemKey = nameKey
      else if (nameKey.includes("scout")) systemKey = "scout"
      else if (nameKey.includes("architect")) systemKey = "architect"
      else if (nameKey.includes("sentinel") || nameKey.includes("sentry")) systemKey = "sentinel"
      else if (nameKey.includes("quartermaster")) systemKey = "quartermaster"

      const mappedStatus = a.status === "running" ? "online" : "standby"

      if (systemKey) {
        // Update existing system agent with database state
        const existing = systemMap.get(systemKey)
        systemMap.set(systemKey, {
          ...existing,
          id: a.id,
          status: mappedStatus,
          role: a.current_activity || existing.role,
          current_activity: a.current_activity,
          progress: a.current_activity,
        })
      } else {
        // Add new custom database agent
        systemMap.set(idKey, {
          id: a.id,
          name: a.name.toUpperCase(),
          role: a.current_activity || "Custom Agent",
          status: mappedStatus,
          current_activity: a.current_activity,
          progress: a.current_activity,
          capabilities: ["Autonomous Run", "Tool Use"]
        })
      }
    })

    return Array.from(systemMap.values())
  }, [rawAgents])

  useFrame((_, delta) => {
    const speedMultiplier = isAnyHovered ? 0.05 : 1.0
    if (a.current) a.current.rotation.z += delta * (0.12 + intensity * 0.25) * speedMultiplier
    if (b.current) b.current.rotation.x += delta * (0.08 + intensity * 0.15) * speedMultiplier
    
    if (groupRef.current) {
      const targetOpacity = state === "idle" || !state ? 1.0 : 0.0
      groupRef.current.traverse((child) => {
        if (child instanceof THREE.Mesh && child.material) {
          if (child.userData.baseOpacity === undefined) {
             child.material.transparent = true
             child.userData.baseOpacity = child.material.opacity || 1.0
          }
          child.material.opacity = THREE.MathUtils.lerp(child.material.opacity, child.userData.baseOpacity * targetOpacity, 0.06)
        }
      })
    }
  })

  return (
    <group ref={groupRef}>
      {/* Torus Ring A (rotated) - Holds orbiting planetary AgentNodes */}
      <group ref={a} rotation={[Math.PI / 2.4, 0, 0]}>
        <mesh>
          <torusGeometry args={[2.4, 0.006, 8, 120]} />
          <meshBasicMaterial color={color} transparent opacity={0.35} />
        </mesh>
        
        {agents.map((agent, index) => {
          const angle = (index / agents.length) * Math.PI * 2
          const x = Math.cos(angle) * 2.4
          const y = Math.sin(angle) * 2.4
          return (
            <AgentNode
              key={agent.id}
              position={[x, y, 0]}
              agent={agent}
              color={color}
              state={state}
              onHoverChange={(hovered) => {
                if (hovered) setHoveredAgentId(agent.id)
                else setHoveredAgentId(null)
              }}
            />
          )
        })}
      </group>

      {/* Torus Ring B (blank for now) */}
      <mesh ref={b} rotation={[0, Math.PI / 3, 0]}>
        <torusGeometry args={[2.8, 0.004, 8, 120]} />
        <meshBasicMaterial color={color} transparent opacity={0.15} />
      </mesh>
    </group>
  )
}

function CoreSphere({ intensity, color, state }: CoreProps) {
  const ref = useRef<THREE.Mesh>(null)
  const groupRef = useRef<THREE.Group>(null)

  useFrame((threeState) => {
    if (ref.current) {
      const s = 1 + Math.sin(threeState.clock.elapsedTime * 1.5) * 0.01 + intensity * 0.02
      ref.current.scale.setScalar(s)
    }
    if (groupRef.current) {
      const targetScale = state === "idle" || !state ? 1.0 : 5.0
      groupRef.current.scale.lerp(new THREE.Vector3(targetScale, targetScale, targetScale), 0.04)
    }
  })

  return (
    <group ref={groupRef}>
      {/* Dark Void Black Hole Core */}
      <mesh ref={ref}>
        <sphereGeometry args={[1.2, 64, 64]} />
        <meshBasicMaterial
          color="#000000"
          depthWrite={true}
        />
      </mesh>
      
      {/* Accretion glow / Event Horizon */}
      <mesh>
        <sphereGeometry args={[1.28, 32, 32]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={0.1 + intensity * 0.2}
          blending={THREE.AdditiveBlending}
          side={THREE.BackSide}
          depthWrite={false}
        />
      </mesh>
    </group>
  )
}

export function NodalCore({ intensity, color, state }: CoreProps) {
  return (
    <>
      <ambientLight intensity={0.4} />
      <pointLight position={[0, 0, 4]} intensity={2} color={color} />
      <CoreSphere intensity={intensity} color={color} state={state} />
      <NodeNetwork intensity={intensity} color={color} state={state} />
      <Rings intensity={intensity} color={color} state={state} />
    </>
  )
}
