import { useCallback, useState } from 'react';
import { systemConfigApi } from '../../api/systemConfig';
import type { TestLLMChannelResponse } from '../../types/systemConfig';
import { useWatchlist } from '../../hooks/useWatchlist';
import {
  PROVIDERS,
  getProvider,
  buildTestPayload,
  deriveWizardSecrets,
  type DsaBridge,
} from './providerConfig';
import { shouldShowWizard } from './shouldShowWizard';

function getDsa(): DsaBridge | undefined {
  return (window as unknown as { dsa?: DsaBridge }).dsa;
}

const STOCK_CODE_RE = /^(?:\d{6}(?:\.\w+)?|[A-Za-z]{1,6}(?:\.\w+)?)$/;

interface TestedProvider {
  provider: (typeof PROVIDERS)[number];
  apiKey: string;
  model: string;
}

export interface SetupWizardProps {
  onClose: () => void;
}

export const SetupWizard: React.FC<SetupWizardProps> = ({ onClose }) => {
  const [step, setStep] = useState(1);
  const [restarting, setRestarting] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);

  // Step 1: LLM channels that passed the connection test.
  const [testedProviders, setTestedProviders] = useState<TestedProvider[]>([]);
  const [providerKey, setProviderKey] = useState(PROVIDERS[0].key);
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState(PROVIDERS[0].defaultModel);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestLLMChannelResponse | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  // Step 2: watchlist chips.
  const { addToWatchlist } = useWatchlist();
  const [chips, setChips] = useState<string[]>([]);
  const [chipInput, setChipInput] = useState('');
  const [chipError, setChipError] = useState<string | null>(null);

  // Step 3: optional skips.
  const [skipNotification, setSkipNotification] = useState(true);
  const [skipSearch, setSkipSearch] = useState(true);

  const currentProvider = PROVIDERS.find((p) => p.key === providerKey) ?? PROVIDERS[0];

  const handleTest = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      const result = await systemConfigApi.testLLMChannel(buildTestPayload(currentProvider, apiKey, model));
      setTestResult(result);
      if (result.success && apiKey) {
        setTestedProviders((prev) => {
          const next = prev.filter((t) => t.provider.key !== currentProvider.key);
          return [...next, { provider: currentProvider, apiKey, model }];
        });
      }
    } catch (e) {
      setTestError(e instanceof Error ? e.message : '连接测试失败');
    } finally {
      setTesting(false);
    }
  }, [apiKey, currentProvider, model]);

  const handleAddChip = useCallback(async () => {
    const code = chipInput.trim().toUpperCase();
    if (!code) return;
    if (!STOCK_CODE_RE.test(code)) {
      setChipError('股票代码格式不正确');
      return;
    }
    if (chips.includes(code)) {
      setChipError('该代码已添加');
      return;
    }
    setChipError(null);
    try {
      await addToWatchlist(code);
      setChips((prev) => [...prev, code]);
      setChipInput('');
    } catch (e) {
      setChipError(e instanceof Error ? e.message : '添加失败');
    }
  }, [chipInput, chips, addToWatchlist]);

  const handleRemoveChip = useCallback((code: string) => {
    setChips((prev) => prev.filter((c) => c !== code));
  }, []);

  const handleFinish = useCallback(async () => {
    setFinishError(null);
    const dsa = getDsa();
    try {
      const secrets: Record<string, string> = {};
      for (const tested of testedProviders) {
        Object.assign(secrets, deriveWizardSecrets(tested.provider, tested.apiKey, tested.model));
      }
      if (dsa?.saveSecrets) {
        await dsa.saveSecrets(secrets);
      }
      setRestarting(true);
      if (dsa?.restartBackend) {
        await dsa.restartBackend();
      }
      onClose();
    } catch (e) {
      setFinishError(e instanceof Error ? e.message : '保存失败');
      setRestarting(false);
    }
  }, [testedProviders, onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl bg-base shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
          <h2 className="text-lg font-semibold text-white">首次启动设置向导</h2>
          <span className="text-sm text-white/50">步骤 {step} / 3</span>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {step === 1 && (
            <div className="space-y-4">
              <p className="text-sm text-white/70">配置大模型通道并测试连通性。测试通过后会加密保存密钥。</p>
              <div className="grid grid-cols-2 gap-3">
                <label className="text-sm text-white/70">
                  服务商
                  <select
                    className="mt-1 w-full rounded bg-white/10 px-2 py-2 text-white"
                    value={providerKey}
                    onChange={(e) => {
                      const p = PROVIDERS.find((x) => x.key === e.target.value) ?? PROVIDERS[0];
                      setProviderKey(p.key);
                      setModel(p.defaultModel);
                      setTestResult(null);
                    }}
                  >
                    {PROVIDERS.map((p) => (
                      <option key={p.key} value={p.key}>{p.label}</option>
                    ))}
                  </select>
                </label>
                <label className="text-sm text-white/70">
                  模型
                  <input
                    className="mt-1 w-full rounded bg-white/10 px-2 py-2 text-white"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                  />
                </label>
              </div>
              <label className="block text-sm text-white/70">
                API Key
                <input
                  type="password"
                  className="mt-1 w-full rounded bg-white/10 px-2 py-2 text-white"
                  value={apiKey}
                  placeholder={currentProvider.envVar}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </label>
              {testedProviders.length > 0 && (
                <div className="text-sm text-emerald-400">
                  已保存密钥：{testedProviders.map((t) => t.provider.envVar).join('、')}
                </div>
              )}
              {testError && <div className="text-sm text-red-400">{testError}</div>}
              {testResult && (
                <div className={`rounded p-3 text-sm ${testResult.success ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
                  {testResult.success
                    ? `连通成功（${testResult.resolvedModel ?? ''}）`
                    : `测试失败：${testResult.message}${testResult.error ? ` (${testResult.error})` : ''}`}
                </div>
              )}
              <button
                type="button"
                className="btn-primary"
                disabled={testing || !apiKey}
                onClick={() => void handleTest()}
              >
                {testing ? '测试中…' : '测试连接'}
              </button>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <p className="text-sm text-white/70">添加自选股（可稍后在首页继续管理）。</p>
              <div className="flex gap-2">
                <input
                  className="flex-1 rounded bg-white/10 px-2 py-2 text-white"
                  value={chipInput}
                  placeholder="例如 600519 或 AAPL"
                  onChange={(e) => setChipInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void handleAddChip(); } }}
                />
                <button type="button" className="btn-secondary" onClick={() => void handleAddChip()}>添加</button>
              </div>
              {chipError && <div className="text-sm text-red-400">{chipError}</div>}
              <div className="flex flex-wrap gap-2">
                {chips.map((c) => (
                  <span key={c} className="flex items-center gap-1 rounded-full bg-white/10 px-3 py-1 text-sm text-white">
                    {c}
                    <button type="button" className="text-white/60 hover:text-white" onClick={() => handleRemoveChip(c)}>×</button>
                  </span>
                ))}
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <p className="text-sm text-white/70">可选配置（可稍后在设置中补充）。</p>
              <label className="flex items-center gap-2 text-sm text-white/80">
                <input type="checkbox" checked={skipNotification} onChange={(e) => setSkipNotification(e.target.checked)} />
                跳过通知渠道配置
              </label>
              <label className="flex items-center gap-2 text-sm text-white/80">
                <input type="checkbox" checked={skipSearch} onChange={(e) => setSkipSearch(e.target.checked)} />
                跳过搜索增强（SearXNG）配置
              </label>
              <p className="text-xs text-white/40">保存后将重启分析引擎使密钥生效。</p>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-white/10 px-6 py-4">
          <button
            type="button"
            className="text-sm text-white/50 hover:text-white"
            onClick={() => setStep((s) => Math.max(1, s - 1))}
            disabled={step === 1}
          >
            上一步
          </button>
          <div className="flex gap-2">
            <button type="button" className="btn-secondary" onClick={onClose}>跳过全部</button>
            {step < 3 ? (
              <button type="button" className="btn-primary" onClick={() => setStep((s) => s + 1)}>下一步</button>
            ) : (
              <button
                type="button"
                className="btn-primary"
                disabled={restarting}
                onClick={() => void handleFinish()}
              >
                {restarting ? '正在重启引擎…' : '完成设置'}
              </button>
            )}
          </div>
        </div>
        {finishError && <div className="px-6 pb-4 text-sm text-red-400">{finishError}</div>}
      </div>
    </div>
  );
};

export default SetupWizard;
