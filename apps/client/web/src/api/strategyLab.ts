import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  EvolveRequest,
  EvolveResponse,
  LabBacktestRequest,
  LabBacktestResponse,
  PublishRequest,
  PublishResponse,
  SignalPreviewRequest,
  SignalPreviewResponse,
  StrategyListItem,
} from '../types/strategyLab';

// ============ API ============

export const strategyLabApi = {
  listStrategies: async (): Promise<StrategyListItem[]> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/strategy-lab/strategies');
    const data = toCamelCase<{ items: StrategyListItem[] }>(response.data);
    return data.items || [];
  },

  previewSignals: async (params: SignalPreviewRequest = {}): Promise<SignalPreviewResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/strategy-lab/signal-preview',
      params.symbols?.length ? params : {},
    );
    return toCamelCase<SignalPreviewResponse>(response.data);
  },

  runBacktest: async (params: LabBacktestRequest): Promise<LabBacktestResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/strategy-lab/backtest',
      params,
    );
    return toCamelCase<LabBacktestResponse>(response.data);
  },

  evolve: async (params: EvolveRequest): Promise<EvolveResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/strategy-lab/evolve',
      params,
    );
    return toCamelCase<EvolveResponse>(response.data);
  },

  publish: async (params: PublishRequest): Promise<PublishResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/strategy-lab/publish',
      params,
    );
    return toCamelCase<PublishResponse>(response.data);
  },
};
