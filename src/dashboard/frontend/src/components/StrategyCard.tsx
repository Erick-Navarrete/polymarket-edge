import type { StrategyInfo } from '../types'
import { toggleStrategy } from '../hooks/useApi'

const ICONS: Record<string, string> = {
  arrows: '⇄',
  lines: '≡',
  copy: '⎘',
  btc: '₿',
  brain: '⚙',
  cloud: '☁',
  gear: '⚙',
}

function stateDot(state: string) {
  const color =
    state === 'RUNNING' ? 'bg-emerald-400' :
    state === 'ERROR' ? 'bg-red-400' :
    'bg-amber-400'
  return <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
}

export default function StrategyCard({ strategy }: { strategy: StrategyInfo }) {
  const pnl = Number(strategy.total_pnl)
  const isRunning = strategy.state === 'RUNNING'

  return (
    <div className={`rounded-lg border transition-colors ${
      isRunning ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-white/10 bg-white/[0.02]'
    } p-4`}>
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/10 text-lg">
            {ICONS[strategy.icon] || ICONS.gear}
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-white">{strategy.name}</h3>
              {stateDot(strategy.state)}
            </div>
            <p className="text-xs text-white/40">{strategy.description}</p>
          </div>
        </div>
        <button
          onClick={() => toggleStrategy(strategy.name, isRunning ? 'stop' : 'start')}
          className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
            isRunning
              ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
              : 'bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30'
          }`}
        >
          {isRunning ? 'Stop' : 'Start'}
        </button>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div>
          <span className="text-white/40">PnL</span>
          <div className={`font-mono ${pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            ${strategy.total_pnl}
          </div>
        </div>
        <div>
          <span className="text-white/40">Exposure</span>
          <div className="font-mono text-white/70">${strategy.total_exposure}</div>
        </div>
        <div>
          <span className="text-white/40">Positions</span>
          <div className="font-mono text-white/70">{strategy.positions}</div>
        </div>
      </div>
    </div>
  )
}
