import StatusHeader from './components/StatusHeader'
import StrategyTable from './components/StrategyTable'
import PositionTable from './components/PositionTable'
import RiskPanel from './components/RiskPanel'
import { useSystemStatus, useStrategies, usePositions, useRisk } from './hooks/useApi'
import { useWebSocket } from './hooks/useWebSocket'
import './index.css'

function App() {
  const status = useSystemStatus()
  const riskFromApi = useRisk()
  const strategies = useStrategies()
  const positions = usePositions()
  const { data: wsData, connected } = useWebSocket(
    `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
  )

  // Merge WebSocket real-time data with REST data — WS takes priority when available
  const risk = wsData?.risk ?? riskFromApi

  const wsStrategies = wsData?.strategies
  const mergedStrategies = wsStrategies
    ? strategies.map((s) => ({
        ...s,
        state: wsStrategies[s.name]?.state ?? s.state,
        total_pnl: wsStrategies[s.name]?.pnl ?? s.total_pnl,
        total_exposure: wsStrategies[s.name]?.exposure ?? s.total_exposure,
      }))
    : strategies

  return (
    <div className="mx-auto flex min-h-screen max-w-7xl flex-col">
      <StatusHeader status={status} risk={risk} wsConnected={connected} />

      <main className="flex-1 space-y-6 p-6">
        <div className="grid grid-cols-[1fr_320px] gap-6">
          <div className="space-y-6">
            <section>
              <h2 className="mb-3 text-sm font-semibold text-white/60">Strategies</h2>
              <StrategyTable strategies={mergedStrategies} />
            </section>

            <section>
              <h2 className="mb-3 text-sm font-semibold text-white/60">Open Positions</h2>
              <PositionTable positions={positions} />
            </section>
          </div>

          <aside>
            <RiskPanel risk={risk} />
          </aside>
        </div>
      </main>

      <footer className="border-t border-white/10 px-6 py-3 text-center text-xs text-white/30">
        Polymarket Edge v{status?.version ?? '—'} &middot;{' '}
        {status?.mode ?? '—'} mode
      </footer>
    </div>
  )
}

export default App
