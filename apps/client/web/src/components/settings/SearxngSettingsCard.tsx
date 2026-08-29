import { useCallback, useEffect, useState } from 'react';

interface SearxngStatus {
  dockerAvailable: boolean;
  containerRunning: boolean;
  healthy: boolean;
  status: 'docker_missing' | 'stopped' | 'unhealthy' | 'running';
  baseUrl?: string;
}

type DsaBridge = {
  searxngStatus?: () => Promise<SearxngStatus>;
  searxngStart?: () => Promise<{ ok: boolean; status: string }>;
  searxngStop?: () => Promise<{ ok: boolean }>;
  restartBackend?: () => Promise<unknown>;
};

function getDsa(): DsaBridge | undefined {
  return (window as unknown as { dsa?: DsaBridge }).dsa;
}

const STATUS_TEXT: Record<SearxngStatus['status'], string> = {
  docker_missing: '未检测到 Docker，可跳过此步骤，使用搜索 Key 兜底',
  stopped: '未运行',
  unhealthy: '容器已启动但健康探测未通过',
  running: '运行中',
};

export const SearxngSettingsCard: React.FC = () => {
  const [status, setStatus] = useState<SearxngStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsRestart, setNeedsRestart] = useState(false);

  const refresh = useCallback(async () => {
    const dsa = getDsa();
    if (!dsa?.searxngStatus) return;
    try {
      setStatus(await dsa.searxngStatus());
    } catch (e) {
      setError(e instanceof Error ? e.message : '状态获取失败');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleStart = useCallback(async () => {
    const dsa = getDsa();
    if (!dsa?.searxngStart) return;
    setBusy(true);
    setError(null);
    try {
      const res = await dsa.searxngStart();
      setNeedsRestart(res.ok);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : '启动失败');
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const handleStop = useCallback(async () => {
    const dsa = getDsa();
    if (!dsa?.searxngStop) return;
    setBusy(true);
    setError(null);
    try {
      await dsa.searxngStop();
      setNeedsRestart(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : '停止失败');
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const handleRestart = useCallback(async () => {
    const dsa = getDsa();
    if (!dsa?.restartBackend) return;
    setBusy(true);
    try {
      await dsa.restartBackend();
      setNeedsRestart(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : '重启失败');
    } finally {
      setBusy(false);
    }
  }, []);

  if (!getDsa()?.searxngStatus) {
    return null;
  }

  const isRunning = status?.status === 'running';

  return (
    <div className="rounded-2xl border settings-border bg-background/40 p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-foreground">搜索增强（SearXNG）</h3>
          <p className="mt-1 text-sm text-muted-text">
            {status ? STATUS_TEXT[status.status] : '检测中…'}
          </p>
        </div>
        <div className="flex gap-2">
          {isRunning ? (
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => void handleStop()}>停止</button>
          ) : (
            <button type="button" className="btn-primary" disabled={busy} onClick={() => void handleStart()}>
              {busy ? '处理中…' : '启动'}
            </button>
          )}
        </div>
      </div>
      {needsRestart && (
        <div className="mt-3 flex items-center gap-3 rounded bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
          <span>搜索已就绪，重启分析引擎使配置生效。</span>
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void handleRestart()}>重启引擎</button>
        </div>
      )}
      {error && <div className="mt-3 text-sm text-red-400">{error}</div>}
    </div>
  );
};

export default SearxngSettingsCard;
