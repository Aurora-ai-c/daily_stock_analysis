export interface StrategyListItem {
  strategyId: string;
  family: string;
  version: number;
  name: string;
  description: string;
  warmupDays: number;
  enabled: boolean;
  disableReason: string;
  tieRule: string;
}

export interface SignalPreviewRequest {
  symbols?: string[];
}

export interface SignalGroup {
  level: string;
  voteRatio: number;
  triggered: number;
  total: number;
  signals: Record<string, unknown>;
}

export interface SignalSymbol {
  name: string;
  st: boolean;
  asOfDate: string;
  groups: Record<string, SignalGroup>;
}

export interface SignalPreviewResponse {
  generatedAt: string;
  asOfDate: string | null;
  strategiesLoaded: number;
  groups: Record<string, string[]>;
  symbols: Record<string, SignalSymbol>;
  dataIssues: Record<string, string>;
  strategyErrors: Record<string, string>[];
  limitBasis: string;
  probeVersion: number;
}

export interface LabBacktestRequest {
  strategyId: string;
  symbols?: string[];
  days: number;
}

export interface LabBacktestResponse {
  strategyId: string;
  name: string;
  dateRange: { start: string; end: string };
  symbols: string[];
  fetchIssues: Record<string, string>;
  overall: {
    winRate: number;
    avgReturn: number;
    avgWinReturn: number;
    avgLossReturn: number;
    profitLossRatio: number;
    maxDrawdown: number;
    sharpeRatio: number;
    signalCount: number;
    avgHoldingDays: number;
    maxConsecutiveLoss: number;
    medianReturn: number;
    totalReturn: number;
  };
  confidenceScore: number;
  perSymbol: Record<string, LabSignal[]>;
  byRegime: { regime: string; winRate: number; avgReturn: number; signalCount: number }[];
}

export interface LabSignal {
  signalDate: string;
  direction: string;
  entryPrice: number;
  exitPrice: number | null;
  exitDate: string | null;
  exitReason: string | null;
  returnPct: number;
  holdingDays: number;
}

export interface EvolveRequest {
  strategyId: string;
  method: 'llm' | 'param_search' | 'hybrid';
  rounds: number;
  samples: number;
}

export interface EvolveResponse {
  family: string;
  sourceStrategyId: string;
  resultStrategyId: string;
  version: number;
  exportedYaml: string | null;
  reportDir: string;
  stdoutTail: string;
  stderrTail: string;
}

export interface PublishRequest {
  strategyIds: string[];
}

export interface PublishResponse {
  items: { strategyId: string; file: string; committed: boolean; pushed: boolean }[];
}
