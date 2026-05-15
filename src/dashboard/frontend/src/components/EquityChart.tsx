import type { EquityPoint } from '../types'
import { useMemo } from 'react'

export default function EquityChart({ history }: { history: EquityPoint[] }) {
  const points = useMemo(() => {
    if (history.length < 2) return ''
    const data = history.map(h => ({ t: h.time, v: Number(h.equity) }))
    const minV = Math.min(...data.map(d => d.v))
    const maxV = Math.max(...data.map(d => d.v))
    const range = maxV - minV || 1
    const w = 280
    const h = 60

    const coords = data.map((d, i) => {
      const x = (i / (data.length - 1)) * w
      const y = h - ((d.v - minV) / range) * (h - 4) - 2
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })

    return coords.join(' ')
  }, [history])

  if (history.length < 2) {
    return (
      <div className="flex h-16 items-center justify-center text-xs text-white/30">
        Waiting for equity data...
      </div>
    )
  }

  const currentEquity = Number(history[history.length - 1].equity)
  const initialEquity = Number(history[0].equity)
  const change = currentEquity - initialEquity
  const isUp = change >= 0

  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-mono text-white">${currentEquity.toFixed(2)}</span>
        <span className={`font-mono ${isUp ? 'text-emerald-400' : 'text-red-400'}`}>
          {isUp ? '+' : ''}{change.toFixed(2)}
        </span>
      </div>
      <svg viewBox="0 0 280 60" className="mt-1 h-16 w-full" preserveAspectRatio="none">
        <polyline
          fill="none"
          stroke={isUp ? '#34d399' : '#f87171'}
          strokeWidth="1.5"
          points={points}
        />
      </svg>
    </div>
  )
}
