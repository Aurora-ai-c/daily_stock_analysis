import { useCallback, useEffect, useState } from 'react';

interface RemoteStatus {
  enabled: boolean;
  lanAddresses: Array<{ interface: string; address: string }>;
  baseUrl: string | null;
}

type DsaBridge = {
  remoteStatus?: () => Promise<RemoteStatus>;
  setRemoteMode?: (enabled: boolean) => Promise<{ ok: boolean; needsRestart?: boolean }>;
  getLanAddresses?: () => Promise<Array<{ interface: string; address: string }>>;
  cloudflaredStart?: () => Promise<{ ok: boolean; url?: string | null; needsDownload?: boolean }>;
  cloudflaredStop?: () => Promise<{ ok: boolean }>;
  restartBackend?: () => Promise<unknown>;
};

function getDsa(): DsaBridge | undefined {
  return (window as unknown as { dsa?: DsaBridge }).dsa;
}

export const RemoteSettingsCard: React.FC = () => {
  const [status, setStatus] = useState<RemoteStatus | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [needsRestart, setNeedsRestart] = useState(false);
  const [tunnelUrl, setTunnelUrl] = useState<string | null>(null);
  const [tunnelBusy, setTunnelBusy] = useState(false);
  const [needsDownload, setNeedsDownload] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const dsa = getDsa();
    if (!dsa?.remoteStatus) return;
    try {
      const s = await dsa.remoteStatus();
      setStatus(s);
      setEnabled(s.enabled);
    } catch (e) {
      setError(e instanceof Error ? e.message : '状态获取失败');
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const toggle = useCallback(async (next: boolean) => {
    const dsa = getDsa();
    if (!dsa?.setRemoteMode) return;
    setError(null);
    try {
      const res = await dsa.setRemoteMode(next);
      setEnabled(next);
      setNeedsRestart(Boolean(res.needsRestart));
    } catch (e) {
      setError(e instanceof Error ? e.message : '切换失败');
    }
  }, []);

  const restart = useCallback(async () => {
    const dsa = getDsa();
    if (!dsa?.restartBackend) return;
    try { await dsa.restartBackend(); setNeedsRestart(false); } catch (e) {
      setError(e instanceof Error ? e.message : '重启失败');
    }
  }, []);

  const startTunnel = useCallback(async () => {
    const dsa = getDsa();
    if (!dsa?.cloudflaredStart) return;
    setTunnelBusy(true);
    setError(null);
    setNeedsDownload(false);
    try {
      const res = await dsa.cloudflaredStart();
      if (res.needsDownload) { setNeedsDownload(true); return; }
      setTunnelUrl(res.url ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '隧道启动失败');
    } finally {
      setTunnelBusy(false);
    }
  }, []);

  const stopTunnel = useCallback(async () => {
    const dsa = getDsa();
    if (!dsa?.cloudflaredStop) return;
    await dsa.cloudflaredStop();
    setTunnelUrl(null);
  }, []);

  const copy = useCallback(async (text: string) => {
    try { await navigator.clipboard.writeText(text); } catch { /* ignore */ }
  }, []);

  if (!getDsa()?.remoteStatus) return null;

  return (
    <div className="rounded-2xl border settings-border bg-background/40 p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-foreground">远程访问（手机/局域网）</h3>
          <p className="mt-1 text-sm text-muted-text">
            {enabled ? '已开启：引擎监听 0.0.0.0，已强制管理员密码' : '仅本机访问'}
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-foreground">
          <input type="checkbox" checked={enabled} onChange={(e) => void toggle(e.target.checked)} />
          开启远程
        </label>
      </div>

      {needsRestart && (
        <div className="mt-3 flex items-center gap-3 rounded bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
          <span>远程模式变更需重启引擎生效。</span>
          <button type="button" className="btn-primary" onClick={() => void restart()}>重启引擎</button>
        </div>
      )}

      {enabled && status?.lanAddresses && status.lanAddresses.length > 0 && (
        <div className="mt-3 space-y-2">
          <p className="text-sm text-muted-text">局域网地址（手机连同一 Wi-Fi 后访问）：</p>
          {status.lanAddresses.map((a) => (
            <div key={`${a.interface}-${a.address}`} className="flex items-center gap-2 text-sm">
              <code className="rounded bg-white/10 px-2 py-1">{`http://${a.address}`}</code>
              <button type="button" className="text-white/60 hover:text-white" onClick={() => void copy(`http://${a.address}`)}>复制</button>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 flex items-center gap-3">
        <button type="button" className="btn-secondary" disabled={tunnelBusy} onClick={() => void startTunnel()}>启动公网隧道</button>
        {tunnelUrl && (
          <button type="button" className="btn-secondary" onClick={() => void stopTunnel()}>停止隧道</button>
        )}
        {needsDownload && <span className="text-sm text-amber-300">首次需下载 cloudflared（约 30MB，校验 sha256）</span>}
      </div>
      {tunnelUrl && (
        <p className="mt-2 break-all text-sm text-emerald-300">公网地址：{tunnelUrl}（手机扫码即可访问，仅本次会话有效）</p>
      )}

      <p className="mt-3 text-xs text-muted-text">
        远程模式启用后，后端强制要求管理员密码（ADMIN_AUTH_ENABLED）。请先在“账户”设置强密码；桌面本机在远程模式下经登录后功能不变。
      </p>
      {error && <div className="mt-2 text-sm text-red-400">{error}</div>}
    </div>
  );
};

export default RemoteSettingsCard;
