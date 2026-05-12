import { useEffect, useState } from 'react'
import type { StrategyInfo, PositionInfo, RiskStatus, SystemStatus } from '../types'

async function api<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export function useSystemStatus() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  useEffect(() => {
    api<SystemStatus>('/').then(setStatus).catch(() => setStatus(null))
    const id = setInterval(() => api<SystemStatus>('/').then(setStatus), 10000)
    return () => clearInterval(id)
  }, [])
  return status
}

export function useStrategies() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  useEffect(() => {
    api<{ strategies: StrategyInfo[] }>('/api/strategies')
      .then((d) => setStrategies(d.strategies))
      .catch(() => setStrategies([]))
    const id = setInterval(
      () => api<{ strategies: StrategyInfo[] }>('/api/strategies').then((d) => setStrategies(d.strategies)),
      5000,
    )
    return () => clearInterval(id)
  }, [])
  return strategies
}

export function usePositions() {
  const [positions, setPositions] = useState<PositionInfo[]>([])
  useEffect(() => {
    api<{ positions: PositionInfo[] }>('/api/positions')
      .then((d) => setPositions(d.positions))
      .catch(() => setPositions([]))
    const id = setInterval(
      () => api<{ positions: PositionInfo[] }>('/api/positions').then((d) => setPositions(d.positions)),
      5000,
    )
    return () => clearInterval(id)
  }, [])
  return positions
}

export function useRisk() {
  const [risk, setRisk] = useState<RiskStatus | null>(null)
  useEffect(() => {
    api<RiskStatus>('/api/risk').then(setRisk).catch(() => setRisk(null))
    const id = setInterval(() => api<RiskStatus>('/api/risk').then(setRisk), 5000)
    return () => clearInterval(id)
  }, [])
  return risk
}

export async function toggleStrategy(name: string, action: 'start' | 'stop') {
  await fetch(`/api/strategies/${name}/${action}`, { method: 'POST' })
}
