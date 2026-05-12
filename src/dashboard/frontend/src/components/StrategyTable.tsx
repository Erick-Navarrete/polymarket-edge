import type { StrategyInfo } from '../types'
import { toggleStrategy } from '../hooks/useApi'

interface Props {
  strategies: StrategyInfo[]
}

function stateColor(state: string) {
  switch (state) {
    case 'RUNNING':
      return 'text-emerald-400'
    case 'STOPPED':
      return 'text-white/40'
    case 'ERROR':
      return 'text-red-400'
    default:
      return 'text-amber-400'
  }
}

function stateBg(state: string) {
  switch (state) {
    case 'RUNNING':
      return 'bg-emerald-500/10'
    case 'STOPPED':
      return 'bg-white/5'
    case 'ERROR':
      return 'bg-red-500/10'
    default:
      return 'bg-amber-500/10'
  }
}

export default function StrategyTable({ strategies }: Props) {
  if (strategies.length === 0) {
    return (
      <div className="rounded-lg border border-white/10 bg-white/5 p-6 text-center text-white/40">
        No strategies loaded
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-lg border border-white/10">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10 bg-white/5 text-left text-white/40">
            <th className="px-4 py-3 font-medium">Strategy</th>
            <th className="px-4 py-3 font-medium">State</th>
            <th className="px-4 py-3 font-medium text-right">PnL</th>
            <th className="px-4 py-3 font-medium text-right">Exposure</th>
            <th className="px-4 py-3 font-medium text-right">Positions</th>
            <th className="px-4 py-3 font-medium text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {strategies.map((s) => {
            const pnl = Number(s.total_pnl)
            return (
              <tr
                key={s.name}
                className={`border-b border-white/5 ${stateBg(s.state)}`}
              >
                <td className="px-4 py-3 font-medium text-white">{s.name}</td>
                <td className={`px-4 py-3 ${stateColor(s.state)}`}>{s.state}</td>
                <td
                  className={`px-4 py-3 text-right font-mono ${
                    pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                  }`}
                >
                  ${s.total_pnl}
                </td>
                <td className="px-4 py-3 text-right font-mono text-white/70">
                  ${s.total_exposure}
                </td>
                <td className="px-4 py-3 text-right font-mono text-white/70">
                  {s.positions}
                </td>
                <td className="px-4 py-3 text-right">
                  {s.state === 'RUNNING' ? (
                    <button
                      onClick={() => toggleStrategy(s.name, 'stop')}
                      className="rounded bg-red-500/20 px-3 py-1 text-xs font-medium text-red-400 hover:bg-red-500/30"
                    >
                      Stop
                    </button>
                  ) : (
                    <button
                      onClick={() => toggleStrategy(s.name, 'start')}
                      className="rounded bg-emerald-500/20 px-3 py-1 text-xs font-medium text-emerald-400 hover:bg-emerald-500/30"
                    >
                      Start
                    </button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
