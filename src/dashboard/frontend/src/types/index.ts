export interface SystemStatus {
  name: string
  version: string
  mode: 'LIVE' | 'PAPER'
}

export interface StrategyInfo {
  name: string
  state: string
  total_pnl: string
  total_exposure: string
  positions: number
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

export interface WsPayload {
  risk: RiskStatus
  strategies: Record<string, { state: string; pnl: string; exposure: string }>
}
