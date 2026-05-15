import type { RiskStatus } from '../types'

interface Props {
  risk: RiskStatus | null
}

function GaugeBar({ label, value, max, unit }: { label: string; value: number; max: number; unit: string }) {
  const pct = Math.min((value / max) * 100, 100)
  const color = pct > 80 ? 'bg-red-500' : pct > 50 ? 'bg-amber-500' : 'bg-emerald-500'

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-white/40">{label}</span>
        <span className="font-mono text-white/70">
          {value.toFixed(1)}{unit}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export default function RiskPanel({ risk }: Props) {
  if (!risk) {
    return (
      <div className="rounded-lg border border-white/10 bg-white/5 p-6 text-center text-white/40">
        Risk data unavailable
      </div>
    )
  }

  const drawdown = Number(risk.drawdown_pct)
  const exposure = Number(risk.total_exposure)
  const equity = Number(risk.current_equity)

  return (
    <div className="space-y-4 rounded-lg border border-white/10 p-4">
      <h2 className="text-sm font-semibold text-white/60">Risk Management</h2>

      {risk.halted && (
        <div className="animate-pulse rounded bg-red-600/20 px-3 py-2 text-xs font-semibold text-red-400">
          SYSTEM HALTED: {risk.halt_reason}
        </div>
      )}

      <div className="space-y-3">
        <GaugeBar label="Drawdown" value={drawdown} max={40} unit="%" />
        <GaugeBar label="Utilization" value={equity > 0 ? (exposure / equity) * 100 : 0} max={100} unit="%" />
      </div>

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <span className="text-white/40">Peak Equity</span>
          <div className="font-mono text-white">${risk.peak_equity}</div>
        </div>
        <div>
          <span className="text-white/40">Current Equity</span>
          <div className="font-mono text-white">${risk.current_equity}</div>
        </div>
        <div>
          <span className="text-white/40">Daily PnL</span>
          <div
            className={`font-mono ${Number(risk.daily_pnl) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}
          >
            ${risk.daily_pnl}
          </div>
        </div>
        <div>
          <span className="text-white/40">Total Exposure</span>
          <div className="font-mono text-white">${risk.total_exposure}</div>
        </div>
      </div>

      <div className="border-t border-white/5 pt-3">
        <h3 className="mb-2 text-xs font-medium text-white/50">Limits</h3>
        <div className="space-y-1 text-[11px] text-white/40">
          <div className="flex justify-between"><span>Daily Loss</span><span className="font-mono">5%</span></div>
          <div className="flex justify-between"><span>Monthly Loss</span><span className="font-mono">15%</span></div>
          <div className="flex justify-between"><span>Drawdown</span><span className="font-mono">25%</span></div>
          <div className="flex justify-between"><span>Max Position</span><span className="font-mono">$500</span></div>
          <div className="flex justify-between"><span>Max Exposure</span><span className="font-mono">$5,000</span></div>
        </div>
      </div>
    </div>
  )
}
