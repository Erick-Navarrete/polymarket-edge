import { useEffect, useState } from 'react'
import type { StrategyInfo, PositionInfo, RiskStatus, SystemStatus, SignalEntry, FillEntry, EquityPoint } from '../types'

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

export function useSignals() {
  const [signals, setSignals] = useState<SignalEntry[]>([])
  useEffect(() => {
    api<{ signals: SignalEntry[] }>('/api/signals')
      .then((d) => setSignals(d.signals))
      .catch(() => setSignals([]))
    const id = setInterval(
      () => api<{ signals: SignalEntry[] }>('/api/signals').then((d) => setSignals(d.signals)),
      5000,
    )
    return () => clearInterval(id)
  }, [])
  return signals
}

export function useFills() {
  const [fills, setFills] = useState<FillEntry[]>([])
  useEffect(() => {
    api<{ fills: FillEntry[] }>('/api/fills')
      .then((d) => setFills(d.fills))
      .catch(() => setFills([]))
    const id = setInterval(
      () => api<{ fills: FillEntry[] }>('/api/fills').then((d) => setFills(d.fills)),
      5000,
    )
    return () => clearInterval(id)
  }, [])
  return fills
}

export function useEquityHistory() {
  const [history, setHistory] = useState<EquityPoint[]>([])
  useEffect(() => {
    api<{ history: EquityPoint[] }>('/api/equity-history')
      .then((d) => setHistory(d.history))
      .catch(() => setHistory([]))
    const id = setInterval(
      () => api<{ history: EquityPoint[] }>('/api/equity-history').then((d) => setHistory(d.history)),
      10000,
    )
    return () => clearInterval(id)
  }, [])
  return history
}

export async function toggleStrategy(name: string, action: 'start' | 'stop') {
  await fetch(`/api/strategies/${name}/${action}`, { method: 'POST' })
}
