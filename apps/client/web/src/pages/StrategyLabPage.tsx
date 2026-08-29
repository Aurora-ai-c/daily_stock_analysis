import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { FlaskConical, Play, RefreshCcw, Rocket, Sparkles } from 'lucide-react';
import { strategyLabApi } from '../api/strategyLab';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import { ApiErrorAlert, Badge, Card, EmptyState } from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { STRATEGY_LAB_TEXT } from '../locales/strategyLabText';
import type {
  EvolveResponse,
  LabBacktestResponse,
  SignalPreviewResponse,
  StrategyListItem,
} from '../types/strategyLab';

const INPUT_CLASS =
  'input-surface input-focus-glow h-10 w-full rounded-xl border bg-transparent px-3 py-2 text-sm transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';

function pct(value?: number | null): string {
  if (value == null) return '--';
  return `${(value * 100).toFixed(1)}%`;
}

function num(value?: number | null, digits = 2): string {
  if (value == null) return '--';
  return value.toFixed(digits);
}

const MetricCell: React.FC<{ label: string; value: string; accent?: boolean }> = ({ label, value, accent }) => (
  <div className="flex flex-col gap-1 rounded-xl border border-border/60 bg-card/60 px-3 py-2">
    <span className="text-xs text-muted-text">{label}</span>
    <span className={`text-base font-semibold tabular-nums ${accent ? 'text-foreground' : 'text-secondary-text'}`}>
      {value}
    </span>
  </div>
);

const StrategyLabPage: React.FC = () => {
  const { language } = useUiLanguage();
  const text = STRATEGY_LAB_TEXT[language];

  const [strategies, setStrategies] = useState<StrategyListItem[]>([]);
  const [loadError, setLoadError] = useState<ParsedApiError | null>(null);

  // Preview
  const [preview, setPreview] = useState<SignalPreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<ParsedApiError | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);

  // Backtest
  const [backtestStrategyId, setBacktestStrategyId] = useState('');
  const [backtestDays, setBacktestDays] = useState(360);
  const [backtest, setBacktest] = useState<LabBacktestResponse | null>(null);
  const [backtestError, setBacktestError] = useState<ParsedApiError | null>(null);
  const [isBacktesting, setIsBacktesting] = useState(false);

  // Evolve
  const [evolveStrategyId, setEvolveStrategyId] = useState('');
  const [evolveMethod, setEvolveMethod] = useState<'llm' | 'param_search' | 'hybrid'>('hybrid');
  const [evolveRounds, setEvolveRounds] = useState(1);
  const [evolveResult, setEvolveResult] = useState<EvolveResponse | null>(null);
  const [evolveError, setEvolveError] = useState<ParsedApiError | null>(null);
  const [isEvolving, setIsEvolving] = useState(false);

  // Publish
  const [selectedForPublish, setSelectedForPublish] = useState<Set<string>>(new Set());
  const [publishResult, setPublishResult] = useState<{ strategyId: string; file: string }[] | null>(null);
  const [publishError, setPublishError] = useState<ParsedApiError | null>(null);
  const [isPublishing, setIsPublishing] = useState(false);

  const fetchStrategies = useCallback(async () => {
    setLoadError(null);
    try {
      setStrategies(await strategyLabApi.listStrategies());
    } catch (err) {
      setLoadError(getParsedApiError(err));
    }
  }, []);

  useEffect(() => {
    void fetchStrategies();
  }, [fetchStrategies]);

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  const handlePreview = async () => {
    setIsPreviewing(true);
    setPreviewError(null);
    setPreview(null);
    try {
      setPreview(await strategyLabApi.previewSignals());
    } catch (err) {
      setPreviewError(getParsedApiError(err));
    } finally {
      setIsPreviewing(false);
    }
  };

  const handleBacktest = async () => {
    if (!backtestStrategyId) return;
    setIsBacktesting(true);
    setBacktestError(null);
    setBacktest(null);
    try {
      setBacktest(await strategyLabApi.runBacktest({
        strategyId: backtestStrategyId,
        days: backtestDays,
      }));
    } catch (err) {
      setBacktestError(getParsedApiError(err));
    } finally {
      setIsBacktesting(false);
    }
  };

  const handleEvolve = async () => {
    if (!evolveStrategyId) return;
    setIsEvolving(true);
    setEvolveError(null);
    setEvolveResult(null);
    try {
      setEvolveResult(await strategyLabApi.evolve({
        strategyId: evolveStrategyId,
        method: evolveMethod,
        rounds: evolveRounds,
        samples: 3,
      }));
      void fetchStrategies();
    } catch (err) {
      setEvolveError(getParsedApiError(err));
    } finally {
      setIsEvolving(false);
    }
  };

  const togglePublishSelection = (strategyId: string) => {
    setSelectedForPublish((prev) => {
      const next = new Set(prev);
      if (next.has(strategyId)) next.delete(strategyId);
      else next.add(strategyId);
      return next;
    });
  };

  const handlePublish = async () => {
    if (selectedForPublish.size === 0) return;
    setIsPublishing(true);
    setPublishError(null);
    setPublishResult(null);
    try {
      const response = await strategyLabApi.publish({ strategyIds: [...selectedForPublish] });
      setPublishResult(response.items.map((item) => ({ strategyId: item.strategyId, file: item.file })));
      setSelectedForPublish(new Set());
    } catch (err) {
      setPublishError(getParsedApiError(err));
    } finally {
      setIsPublishing(false);
    }
  };

  const enabledStrategies = strategies.filter((item) => item.enabled);

  return (
    <div className="min-h-full flex flex-col gap-4 p-3 sm:p-4">
      {loadError && <ApiErrorAlert error={loadError} />}

      {/* 1. Strategy list */}
      <Card variant="gradient" padding="md">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-primary" />
            <span className="label-uppercase">{text.strategiesTitle}</span>
          </div>
          <button
            type="button"
            onClick={() => void fetchStrategies()}
            className="btn-secondary flex items-center gap-1.5"
            aria-label={text.refresh}
          >
            <RefreshCcw className="h-3.5 w-3.5" />
            {text.refresh}
          </button>
        </div>
        {strategies.length === 0 && !loadError ? (
          <EmptyState title={text.noData} description={text.noData} className="border-dashed" />
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {strategies.map((item) => (
              <div
                key={item.strategyId}
                className="flex flex-col gap-2 rounded-xl border border-border/60 bg-card/60 p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-semibold text-foreground">{item.name || item.strategyId}</span>
                  {item.enabled ? (
                    <Badge variant="success">{text.enabled}</Badge>
                  ) : (
                    <Badge variant="warning">{text.disabled}</Badge>
                  )}
                </div>
                <span className="truncate font-mono text-xs text-secondary-text">{item.strategyId}</span>
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-text">
                  <span>{text.family}: {item.family}</span>
                  <span>{text.version}: v{item.version}</span>
                  <span>{text.warmup}: {item.warmupDays}d</span>
                  <span>{text.tieRule}: {item.tieRule}</span>
                </div>
                {!item.enabled && item.disableReason ? (
                  <span className="text-xs text-warning">{item.disableReason}</span>
                ) : null}
                <label className="mt-auto flex cursor-pointer items-center gap-2 text-xs text-secondary-text">
                  <input
                    type="checkbox"
                    checked={selectedForPublish.has(item.strategyId)}
                    onChange={() => togglePublishSelection(item.strategyId)}
                    disabled={isPublishing}
                  />
                  {text.publishTitle}
                </label>
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* 2. Signal preview */}
        <Card padding="md">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            <span className="label-uppercase">{text.previewTitle}</span>
          </div>
          <p className="mb-3 text-xs text-muted-text">{text.previewHint}</p>
          <button
            type="button"
            onClick={() => void handlePreview()}
            disabled={isPreviewing}
            className="btn-primary flex items-center gap-1.5"
          >
            {isPreviewing ? (
              <>
                <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                {text.previewRunning}
              </>
            ) : (
              text.previewRun
            )}
          </button>
          {previewError && <ApiErrorAlert error={previewError} className="mt-3" />}
          {preview ? (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted-text">
                    <th className="py-2 pr-2">#</th>
                    <th className="py-2 pr-2">股票</th>
                    <th className="py-2 pr-2">分组信号</th>
                    <th className="py-2">级别（vote_ratio）</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(preview.symbols).map(([code, stock], idx) => (
                    <tr key={code} className="border-t border-border/50 align-top">
                      <td className="py-2 pr-2 text-muted-text">{idx + 1}</td>
                      <td className="py-2 pr-2">
                        <div className="flex flex-col">
                          <span className="font-mono text-xs">{code}</span>
                          <span className="text-xs text-muted-text">{stock.name}</span>
                          {stock.st ? <Badge variant="warning">ST</Badge> : null}
                        </div>
                      </td>
                      <td className="py-2 pr-2">
                        <div className="flex flex-col gap-1">
                          {Object.entries(stock.groups).map(([group, g]) => (
                            <div key={group} className="flex items-center gap-2">
                              <Badge variant={g.level === 'strong' ? 'success' : g.level === 'moderate' ? 'warning' : 'default'}>
                                {g.level}
                              </Badge>
                              <span className="text-xs text-secondary-text">{group}</span>
                              <span className="font-mono text-xs text-muted-text">
                                {g.triggered}/{g.total}
                              </span>
                            </div>
                          ))}
                          {Object.keys(stock.groups).length === 0 ? (
                            <span className="text-xs text-muted-text">{text.noData}</span>
                          ) : null}
                        </div>
                      </td>
                      <td className="py-2 text-secondary-text">
                        {Object.entries(stock.groups).map(([group, g]) => (
                          <div key={group} className="font-mono text-xs">
                            {preview.groups[group]?.join(', ') || group} = {g.voteRatio}
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-3 text-xs text-muted-text">
                {text.signalDate}: {preview.asOfDate ?? '--'} · limit_basis={preview.limitBasis}
              </p>
              {Object.keys(preview.dataIssues).length > 0 ? (
                <p className="mt-3 text-xs text-warning">
                  {text.fetchIssues}: {Object.entries(preview.dataIssues).map(([code, issue]) => `${code}: ${issue}`).join('; ')}
                </p>
              ) : null}
              {preview.strategyErrors.length > 0 ? (
                <p className="mt-3 text-xs text-danger">
                  {preview.strategyErrors.map((item) => `${item.symbol}/${item.strategy_id}: ${item.error}`).join('; ')}
                </p>
              ) : null}
            </div>
          ) : (
            !previewError && (
              <EmptyState title={text.noPreview} description={text.noPreview} className="mt-4 border-dashed" />
            )
          )}
        </Card>

        {/* 3. One-click backtest */}
        <Card padding="md">
          <div className="mb-3 flex items-center gap-2">
            <Play className="h-4 w-4 text-primary" />
            <span className="label-uppercase">{text.backtestTitle}</span>
          </div>
          <p className="mb-3 text-xs text-muted-text">{text.backtestHint}</p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[180px] flex-1">
              <span className="mb-1 block text-xs text-muted-text">{text.strategy}</span>
              <select
                value={backtestStrategyId}
                onChange={(e) => setBacktestStrategyId(e.target.value)}
                disabled={isBacktesting}
                className={INPUT_CLASS}
              >
                <option value="">{text.selectStrategy}</option>
                {enabledStrategies.map((item) => (
                  <option key={item.strategyId} value={item.strategyId}>{item.name || item.strategyId}</option>
                ))}
              </select>
            </div>
            <div className="w-36">
              <span className="mb-1 block text-xs text-muted-text">{text.windowDays}</span>
              <input
                type="number"
                min={30}
                max={1095}
                value={backtestDays}
                onChange={(e) => setBacktestDays(Number(e.target.value) || 360)}
                disabled={isBacktesting}
                className={`${INPUT_CLASS} tabular-nums`}
              />
            </div>
            <button
              type="button"
              onClick={() => void handleBacktest()}
              disabled={isBacktesting || !backtestStrategyId}
              className="btn-primary flex items-center gap-1.5"
            >
              {isBacktesting ? text.backtestRunning : text.backtestRun}
            </button>
          </div>
          {backtestError && <ApiErrorAlert error={backtestError} className="mt-3" />}
          {backtest ? (
            <div className="mt-4 flex flex-col gap-3">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                <MetricCell label={text.winRate} value={pct(backtest.overall.winRate)} accent />
                <MetricCell label={text.avgReturn} value={pct(backtest.overall.avgReturn)} />
                <MetricCell label={text.totalReturn} value={pct(backtest.overall.totalReturn)} />
                <MetricCell label={text.maxDrawdown} value={pct(backtest.overall.maxDrawdown)} />
                <MetricCell label={text.sharpe} value={num(backtest.overall.sharpeRatio)} />
                <MetricCell label={text.profitLossRatio} value={num(backtest.overall.profitLossRatio)} />
                <MetricCell label={text.avgHoldingDays} value={num(backtest.overall.avgHoldingDays, 1)} />
                <MetricCell label={text.confidence} value={pct(backtest.confidenceScore)} />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted-text">
                      <th className="py-2 pr-2">{text.signalDate}</th>
                      <th className="py-2 pr-2">{text.direction}</th>
                      <th className="py-2 pr-2">{text.entry}</th>
                      <th className="py-2 pr-2">{text.exit}</th>
                      <th className="py-2 pr-2">{text.exitReason}</th>
                      <th className="py-2 pr-2">{text.returnPct}</th>
                      <th className="py-2">{text.holdingDays}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(backtest.perSymbol).map(([code, signals]) => (
                      signals.length === 0 ? (
                        <tr key={code} className="border-t border-border/50">
                          <td colSpan={7} className="py-2 font-mono text-xs text-muted-text">
                            {code}: {text.noSignals}
                          </td>
                        </tr>
                      ) : (
                        signals.map((sig, i) => (
                          <tr key={`${code}-${sig.signalDate}-${i}`} className="border-t border-border/50">
                            <td className="py-2 pr-2">
                              <span className="font-mono text-xs text-secondary-text">{code}</span>
                              <span className="ml-2 text-xs">{sig.signalDate}</span>
                            </td>
                            <td className="py-2 pr-2">{sig.direction === 'long' ? text.long : sig.direction}</td>
                            <td className="py-2 pr-2 tabular-nums">{num(sig.entryPrice)}</td>
                            <td className="py-2 pr-2 tabular-nums">{sig.exitPrice != null ? num(sig.exitPrice) : '--'}</td>
                            <td className="py-2 pr-2 text-secondary-text">{sig.exitReason || '--'}</td>
                            <td className={`py-2 pr-2 tabular-nums ${sig.returnPct > 0 ? 'text-success' : sig.returnPct < 0 ? 'text-danger' : ''}`}>
                              {pct(sig.returnPct)}
                            </td>
                            <td className="py-2 tabular-nums">{sig.holdingDays}</td>
                          </tr>
                        ))
                      )
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            !backtestError && (
              <EmptyState title={text.noBacktest} description={text.noBacktest} className="mt-4 border-dashed" />
            )
          )}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* 4. Evolution */}
        <Card padding="md">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            <span className="label-uppercase">{text.evolveTitle}</span>
          </div>
          <p className="mb-3 text-xs text-muted-text">{text.evolveHint}</p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[180px] flex-1">
              <span className="mb-1 block text-xs text-muted-text">{text.strategy}</span>
              <select
                value={evolveStrategyId}
                onChange={(e) => setEvolveStrategyId(e.target.value)}
                disabled={isEvolving}
                className={INPUT_CLASS}
              >
                <option value="">{text.selectStrategy}</option>
                {enabledStrategies.map((item) => (
                  <option key={item.strategyId} value={item.strategyId}>{item.name || item.strategyId}</option>
                ))}
              </select>
            </div>
            <div className="w-40">
              <span className="mb-1 block text-xs text-muted-text">{text.method}</span>
              <select
                value={evolveMethod}
                onChange={(e) => setEvolveMethod(e.target.value as typeof evolveMethod)}
                disabled={isEvolving}
                className={INPUT_CLASS}
              >
                <option value="hybrid">hybrid</option>
                <option value="llm">llm</option>
                <option value="param_search">param_search</option>
              </select>
            </div>
            <div className="w-24">
              <span className="mb-1 block text-xs text-muted-text">{text.rounds}</span>
              <input
                type="number"
                min={1}
                max={5}
                value={evolveRounds}
                onChange={(e) => setEvolveRounds(Number(e.target.value) || 1)}
                disabled={isEvolving}
                className={`${INPUT_CLASS} tabular-nums`}
              />
            </div>
            <button
              type="button"
              onClick={() => void handleEvolve()}
              disabled={isEvolving || !evolveStrategyId}
              className="btn-primary flex items-center gap-1.5"
            >
              {isEvolving ? text.evolveRunning : text.evolveRun}
            </button>
          </div>
          {evolveError && <ApiErrorAlert error={evolveError} className="mt-3" />}
          {evolveResult ? (
            <div className="mt-4 flex flex-col gap-2 rounded-xl border border-border/60 bg-card/60 p-3 text-sm">
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span className="text-secondary-text">
                  {text.resultStrategy}: <span className="font-mono text-foreground">{evolveResult.resultStrategyId}</span>
                </span>
                <span className="text-secondary-text">{text.version}: v{evolveResult.version}</span>
                {evolveResult.exportedYaml && (
                  <span className="text-success">
                    {text.exported}: <span className="font-mono">{evolveResult.exportedYaml}</span>
                  </span>
                )}
              </div>
              <span className="text-xs text-muted-text">
                {text.reportDir}: <span className="font-mono">{evolveResult.reportDir}</span>
              </span>
              {evolveResult.stdoutTail ? (
                <pre className="max-h-40 overflow-auto rounded-lg bg-black/20 p-2 text-xs text-secondary-text">
                  {evolveResult.stdoutTail}
                </pre>
              ) : null}
            </div>
          ) : null}
        </Card>

        {/* 5. Publish */}
        <Card padding="md">
          <div className="mb-3 flex items-center gap-2">
            <Rocket className="h-4 w-4 text-primary" />
            <span className="label-uppercase">{text.publishTitle}</span>
          </div>
          <p className="mb-3 text-xs text-muted-text">{text.publishHint}</p>
          <button
            type="button"
            onClick={() => void handlePublish()}
            disabled={isPublishing || selectedForPublish.size === 0}
            className="btn-primary flex items-center gap-1.5"
          >
            {isPublishing ? text.publishRunning : text.publishRun}
          </button>
          {selectedForPublish.size === 0 && !isPublishing && (
            <p className="mt-2 text-xs text-muted-text">{text.publishSelect}</p>
          )}
          {publishError && <ApiErrorAlert error={publishError} className="mt-3" />}
          {publishResult && (
            <div className="mt-3 flex flex-col gap-1 text-sm">
              <span className="text-success">{text.published}</span>
              {publishResult.map((item) => (
                <span key={item.strategyId} className="font-mono text-xs text-secondary-text">
                  {item.strategyId} → {item.file}
                </span>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
};

export default StrategyLabPage;
