import type { SystemStatus, RiskStatus } from '../types'

interface Props {
  status: SystemStatus | null
  risk: RiskStatus | null
  wsConnected: boolean
}

export default function StatusHeader({ status, risk, wsConnected }: Props) {
  const mode = status?.mode ?? '—'
  const isLive = mode === 'LIVE'

  return (
    <header className="flex items-center justify-between border-b border-white/10 px-6 py-4">
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-bold tracking-tight text-white">Polymarket Edge</h1>
        <span
          className={`rounded px-2 py-0.5 text-xs font-semibold ${
            isLive ? 'bg-red-500/20 text-red-400' : 'bg-emerald-500/20 text-emerald-400'
          }`}
        >
          {mode}
        </span>
      </div>

      <div className="flex items-center gap-6 text-sm">
        {risk && (
          <>
            <div>
              <span className="text-white/40">Equity</span>{' '}
              <span className="font-mono text-white">${risk.current_equity}</span>
            </div>
            <div>
              <span className="text-white/40">Daily PnL</span>{' '}
              <span
                className={`font-mono ${
                  Number(risk.daily_pnl) >= 0 ? 'text-emerald-400' : 'text-red-400'
                }`}
              >
                ${risk.daily_pnl}
              </span>
            </div>
            <div>
              <span className="text-white/40">Drawdown</span>{' '}
              <span className="font-mono text-amber-400">{risk.drawdown_pct}%</span>
            </div>
          </>
        )}

        {risk?.halted && (
          <span className="animate-pulse rounded bg-red-600 px-2 py-0.5 text-xs font-bold text-white">
            HALTED: {risk.halt_reason}
          </span>
        )}

        <div className="flex items-center gap-1.5">
          <span
            className={`h-2 w-2 rounded-full ${wsConnected ? 'bg-emerald-400' : 'bg-red-400'}`}
          />
          <span className="text-white/40">{wsConnected ? 'Live' : 'Disconnected'}</span>
        </div>
      </div>
    </header>
  )
}
