"use client"

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"

type CoreProps = {
  /** 0 = idle, 1 = fully energized (listening / responding) */
  intensity: number
  color: string
}

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

function NodeNetwork({ intensity, color }: CoreProps) {
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

  useFrame((state, delta) => {
    if (!group.current) return
    const t = state.clock.elapsedTime
    const speed = 0.05 + intensity * 0.25
    group.current.rotation.y += delta * speed
    group.current.rotation.x = Math.sin(t * 0.2) * 0.15

    const pulse = 1 + Math.sin(t * (1.5 + intensity * 3)) * (0.015 + intensity * 0.05)
    group.current.scale.setScalar(pulse)

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
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </points>
      <lineSegments ref={linesRef} geometry={lineGeometry}>
        <lineBasicMaterial color={color} transparent opacity={0.2} blending={THREE.AdditiveBlending} />
      </lineSegments>
    </group>
  )
}

function Rings({ intensity, color }: CoreProps) {
  const a = useRef<THREE.Mesh>(null)
  const b = useRef<THREE.Mesh>(null)
  useFrame((_, delta) => {
    if (a.current) a.current.rotation.z += delta * (0.2 + intensity * 0.4)
    if (b.current) b.current.rotation.x += delta * (0.15 + intensity * 0.3)
  })
  return (
    <group>
      <mesh ref={a} rotation={[Math.PI / 2.4, 0, 0]}>
        <torusGeometry args={[2.4, 0.006, 8, 120]} />
        <meshBasicMaterial color={color} transparent opacity={0.35} />
      </mesh>
      <mesh ref={b} rotation={[0, Math.PI / 3, 0]}>
        <torusGeometry args={[2.7, 0.004, 8, 120]} />
        <meshBasicMaterial color={color} transparent opacity={0.2} />
      </mesh>
    </group>
  )
}

function Glow({ intensity, color }: CoreProps) {
  const ref = useRef<THREE.Mesh>(null)
  useFrame((state) => {
    if (!ref.current) return
    const s = 1 + Math.sin(state.clock.elapsedTime * 1.5) * 0.05 + intensity * 0.1
    ref.current.scale.setScalar(s)
    const mat = ref.current.material as THREE.MeshBasicMaterial
    mat.opacity = 0.08 + intensity * 0.18
  })
  return (
    <mesh ref={ref}>
      <sphereGeometry args={[1.1, 32, 32]} />
      <meshBasicMaterial color={color} transparent opacity={0.12} blending={THREE.AdditiveBlending} />
    </mesh>
  )
}

export function NodalCore({ intensity, color }: CoreProps) {
  return (
    <>
      <ambientLight intensity={0.4} />
      <pointLight position={[0, 0, 4]} intensity={2} color={color} />
      <Glow intensity={intensity} color={color} />
      <NodeNetwork intensity={intensity} color={color} />
      <Rings intensity={intensity} color={color} />
    </>
  )
}
