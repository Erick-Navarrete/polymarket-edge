export interface SystemStatus {
  name: string
  version: string
  mode: 'LIVE' | 'SHADOW' | 'PAPER'
}

export interface StrategyInfo {
  name: string
  state: string
  total_pnl: string
  total_exposure: string
  positions: number
  description: string
  icon: string
}

export interface PositionInfo {
  strategy: string
  condition_id: string
  side: string
  entry_price: string
  size: string
  current_price: string
  unrealized_pnl: string
}

export interface RiskStatus {
  halted: boolean
  halt_reason: string | null
  daily_pnl: string
  peak_equity: string
  current_equity: string
  total_exposure: string
  drawdown_pct: string
}

export interface SignalEntry {
  time: number
  strategy: string
  condition_id: string
  side: string
  price: string
  size: string
  reason: string
  confidence: number
}

export interface FillEntry {
  time: number
  strategy: string
  side: string
  price: string
  fill_price: string
  size: string
  pnl: string
}

export interface EquityPoint {
  time: number
  equity: string
}

export interface WsPayload {
  risk: RiskStatus
  strategies: Record<string, StrategyWsData>
  recent_signals: SignalEntry[]
  recent_fills: FillEntry[]
  equity_history: EquityPoint[]
}

export interface StrategyWsData {
  state: string
  pnl: string
  exposure: string
  positions: number
  description: string
  icon: string
}
