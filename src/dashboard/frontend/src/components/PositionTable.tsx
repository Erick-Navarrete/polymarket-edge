import type { PositionInfo } from '../types'

interface Props {
  positions: PositionInfo[]
}

export default function PositionTable({ positions }: Props) {
  if (positions.length === 0) {
    return (
      <div className="rounded-lg border border-white/10 bg-white/5 p-6 text-center text-white/40">
        No open positions
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-lg border border-white/10">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10 bg-white/5 text-left text-white/40">
            <th className="px-4 py-3 font-medium">Strategy</th>
            <th className="px-4 py-3 font-medium">Condition</th>
            <th className="px-4 py-3 font-medium">Side</th>
            <th className="px-4 py-3 font-medium text-right">Entry</th>
            <th className="px-4 py-3 font-medium text-right">Current</th>
            <th className="px-4 py-3 font-medium text-right">Size</th>
            <th className="px-4 py-3 font-medium text-right">Unrealized PnL</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p, i) => {
            const upnl = Number(p.unrealized_pnl)
            return (
              <tr key={`${p.condition_id}-${i}`} className="border-b border-white/5">
                <td className="px-4 py-3 font-medium text-white">{p.strategy}</td>
                <td className="px-4 py-3 font-mono text-xs text-white/60">
                  {p.condition_id.slice(0, 8)}...
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                      p.side === 'BUY'
                        ? 'bg-emerald-500/20 text-emerald-400'
                        : 'bg-red-500/20 text-red-400'
                    }`}
                  >
                    {p.side}
                  </span>
                </td>
                <td className="px-4 py-3 text-right font-mono text-white/70">
                  ${p.entry_price}
                </td>
                <td className="px-4 py-3 text-right font-mono text-white/70">
                  ${p.current_price}
                </td>
                <td className="px-4 py-3 text-right font-mono text-white/70">
                  {p.size}
                </td>
                <td
                  className={`px-4 py-3 text-right font-mono ${
                    upnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                  }`}
                >
                  ${p.unrealized_pnl}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
