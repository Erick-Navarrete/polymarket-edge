import type { SignalEntry, FillEntry } from '../types'

function timeAgo(ts: number): string {
  const secs = Math.floor(Date.now() / 1000 - ts)
  if (secs < 0) return 'now'
  if (secs < 60) return `${secs}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m`
  return `${Math.floor(secs / 3600)}h`
}

function sideBadge(side: string) {
  const isBuy = side.startsWith('BUY')
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
      isBuy ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'
    }`}>
      {side.replace('BUY_', '').replace('SELL_', '')}
    </span>
  )
}

export default function SignalFeed({ signals, fills }: { signals: SignalEntry[]; fills: FillEntry[] }) {
  const recent = signals.slice(-15).reverse()

  return (
    <div className="space-y-1.5">
      {recent.length === 0 && (
        <div className="py-6 text-center text-xs text-white/30">No signals yet</div>
      )}
      {recent.map((sig, i) => {
        const fill = fills.find(f => f.time === sig.time && f.strategy === sig.strategy)
        return (
          <div key={`${sig.time}-${i}`} className="flex items-center gap-2 rounded bg-white/[0.02] px-3 py-2 text-xs">
            <span className="w-14 shrink-0 text-white/30">{timeAgo(sig.time)}</span>
            <span className="w-20 shrink-0 font-medium text-white/60 truncate">{sig.strategy}</span>
            {sideBadge(sig.side)}
            <span className="font-mono text-white/50">${sig.price}</span>
            <span className="truncate text-white/30" title={sig.reason}>{sig.reason}</span>
            {fill && (
              <span className={`ml-auto shrink-0 font-mono ${
                Number(fill.pnl) >= 0 ? 'text-emerald-400' : 'text-red-400'
              }`}>
                {Number(fill.pnl) >= 0 ? '+' : ''}{fill.pnl}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}
