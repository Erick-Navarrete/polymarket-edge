import StatusHeader from './components/StatusHeader'
import StrategyCard from './components/StrategyCard'
import PositionTable from './components/PositionTable'
import RiskPanel from './components/RiskPanel'
import SignalFeed from './components/SignalFeed'
import EquityChart from './components/EquityChart'
import { useSystemStatus, useStrategies, usePositions, useRisk, useSignals, useFills, useEquityHistory } from './hooks/useApi'
import { useWebSocket } from './hooks/useWebSocket'
import './index.css'

function App() {
  const status = useSystemStatus()
  const riskFromApi = useRisk()
  const strategies = useStrategies()
  const positions = usePositions()
  const apiSignals = useSignals()
  const apiFills = useFills()
  const apiEquity = useEquityHistory()
  const { data: wsData, connected } = useWebSocket(
    `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
  )

  // Merge WebSocket real-time data with REST data
  const risk = wsData?.risk ?? riskFromApi

  const wsStrategies = wsData?.strategies
  const mergedStrategies = wsStrategies
    ? strategies.map((s) => ({
        ...s,
        state: wsStrategies[s.name]?.state ?? s.state,
        total_pnl: wsStrategies[s.name]?.pnl ?? s.total_pnl,
        total_exposure: wsStrategies[s.name]?.exposure ?? s.total_exposure,
        positions: wsStrategies[s.name]?.positions ?? s.positions,
        description: wsStrategies[s.name]?.description ?? s.description,
        icon: wsStrategies[s.name]?.icon ?? s.icon,
      }))
    : strategies

  const signals = (wsData?.recent_signals ?? apiSignals).slice(-15)
  const fills = wsData?.recent_fills ?? apiFills
  const equityHistory = wsData?.equity_history ?? apiEquity

  return (
    <div className="mx-auto flex min-h-screen max-w-7xl flex-col">
      <StatusHeader status={status} risk={risk} wsConnected={connected} />

      <main className="flex-1 space-y-6 p-6">
        {/* Strategy Cards */}
        <section>
          <h2 className="mb-3 text-sm font-semibold text-white/60">Strategies</h2>
          <div className="grid grid-cols-3 gap-3">
            {mergedStrategies.map((s) => (
              <StrategyCard key={s.name} strategy={s} />
            ))}
          </div>
          {mergedStrategies.length === 0 && (
            <div className="rounded-lg border border-white/10 bg-white/5 p-6 text-center text-white/40">
              No strategies loaded — start the backend with strategies enabled
            </div>
          )}
        </section>

        <div className="grid grid-cols-[1fr_320px] gap-6">
          <div className="space-y-6">
            {/* Equity Chart + Signal Feed */}
            <section>
              <h2 className="mb-3 text-sm font-semibold text-white/60">Equity & Signals</h2>
              <div className="grid grid-cols-[1fr_1fr] gap-3">
                <div className="rounded-lg border border-white/10 p-4">
                  <EquityChart history={equityHistory} />
                </div>
                <div className="max-h-60 overflow-y-auto rounded-lg border border-white/10 p-3">
                  <SignalFeed signals={signals} fills={fills} />
                </div>
              </div>
            </section>

            {/* Open Positions */}
            <section>
              <h2 className="mb-3 text-sm font-semibold text-white/60">Open Positions</h2>
              <PositionTable positions={positions} />
            </section>
          </div>

          <aside className="space-y-4">
            <RiskPanel risk={risk} />
          </aside>
        </div>
      </main>

      <footer className="border-t border-white/10 px-6 py-3 text-center text-xs text-white/30">
        Polymarket Edge v{status?.version ?? '——'} &middot;{' '}
        {status?.mode ?? '——'} mode
      </footer>
    </div>
  )
}

export default App
