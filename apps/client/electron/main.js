'use strict';

const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const net = require('net');
const http = require('http');

const keyVault = require('./keyVault');
const searxng = require('./searxng');
const remote = require('./remote');

const SEARXNG_RESERVED_PORT = 8080;
const DEFAULT_BIND_HOST = '127.0.0.1';
const HEALTH_PATH = '/api/health';
const HEALTH_TIMEOUT_MS = 120000;
const HEALTH_INTERVAL_MS = 250;
const HEALTH_REQUEST_TIMEOUT_MS = 1500;
const PORT_RANGE_START = 8000;
const PORT_RANGE_END = 8100;
const PUBLIC_BIND_HOSTS = new Set(['0.0.0.0', '::', '[::]', '*']);

let electron = null;
function loadElectron() {
  if (electron) return electron;
  electron = require('electron');
  return electron;
}

function normalizeBackendHost(value, fallback = DEFAULT_BIND_HOST) {
  const v = String(value || '').trim();
  return v || fallback;
}

function normalizeBackendBindHost(value, fallback = DEFAULT_BIND_HOST) {
  const host = normalizeBackendHost(value, fallback);
  const lower = host.toLowerCase();
  if (lower === '*') return '0.0.0.0';
  if (lower === '[::]') return '::';
  return host;
}

function resolveConnectHost(bindHost) {
  const host = normalizeBackendBindHost(bindHost, DEFAULT_BIND_HOST);
  if (PUBLIC_BIND_HOSTS.has(host.toLowerCase())) return DEFAULT_BIND_HOST;
  return host;
}

function buildBackendArgs({ host, port }) {
  return [
    '--serve-only',
    '--host', normalizeBackendBindHost(host),
    '--port', String(port),
  ];
}

function buildBackendEnvironment({ envFile, dbPath, logDir, port = null, host = null, sourceEnv = process.env, secrets = {}, searxng: searxngState = null, remote: remoteState = null } = {}) {
  const remoteEnabled = !!(remoteState && remoteState.enabled);
  const selectedHost = remoteEnabled ? '0.0.0.0' : (normalizeBackendBindHost(host) || DEFAULT_BIND_HOST);
  const env = {
    ...sourceEnv,
    DSA_DESKTOP_MODE: 'true',
    ENV_FILE: envFile || '',
    DATABASE_PATH: dbPath || '',
    LOG_DIR: logDir || '',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    WEBUI_HOST: selectedHost,
    ADMIN_AUTH_ENABLED: remoteEnabled ? 'true' : 'false',
    WEBUI_ENABLED: 'false',
    BOT_ENABLED: 'false',
    DINGTALK_STREAM_ENABLED: 'false',
    FEISHU_STREAM_ENABLED: 'false',
  };
  if (Number.isInteger(port) && port >= 1 && port <= 65535) {
    env.WEBUI_PORT = String(port);
  }
  Object.assign(env, secrets);
  Object.assign(env, searxng.assembleSearxngEnv(searxngState && searxngState.baseUrls));
  return env;
}

function buildBackendUrl(host, port, pathname = '/') {
  const url = new URL(`http://${host}:${port}/`);
  url.pathname = pathname;
  return url.toString();
}

function resolveAppRoot() {
  const { app } = loadElectron();
  if (app.isPackaged) {
    return path.resolve(process.resourcesPath, '..', '..');
  }
  return path.resolve(__dirname, '..', '..', '..');
}

function resolveDataDir() {
  const { app } = loadElectron();
  return path.join(app.getPath('appData'), 'DSA');
}

function resolveEnvExamplePath() {
  const { app } = loadElectron();
  if (app.isPackaged) {
    return path.join(process.resourcesPath, '.env.example');
  }
  return path.join(resolveAppRoot(), '.env.example');
}

function ensureEnvFile(envPath) {
  if (fs.existsSync(envPath)) return;
  const example = resolveEnvExamplePath();
  if (fs.existsSync(example)) {
    fs.copyFileSync(example, envPath);
    return;
  }
  fs.writeFileSync(envPath, '# Configure your API keys and stock list here.\n', 'utf-8');
}

function resolvePythonPath(appRoot) {
  if (process.platform === 'win32') {
    const venv = path.join(appRoot, '.venv', 'Scripts', 'python.exe');
    if (fs.existsSync(venv)) return venv;
  }
  const venvSh = path.join(appRoot, '.venv', 'bin', 'python');
  if (fs.existsSync(venvSh)) return venvSh;
  return process.env.DSA_PYTHON || 'python';
}

function resolveBackendPath() {
  const { app } = loadElectron();
  if (process.env.DSA_BACKEND_PATH) return process.env.DSA_BACKEND_PATH;
  if (!app.isPackaged) return null;
  const backendDir = path.join(process.resourcesPath, 'backend', 'stock_analysis');
  const exe = process.platform === 'win32' ? 'stock_analysis.exe' : 'stock_analysis';
  const oneDir = path.join(backendDir, exe);
  if (fs.existsSync(oneDir)) return oneDir;
  return path.join(process.resourcesPath, 'backend', exe);
}

function findAvailablePort({ startPort = PORT_RANGE_START, endPort = PORT_RANGE_END, exclude = [SEARXNG_RESERVED_PORT], host = DEFAULT_BIND_HOST } = {}) {
  const excluded = new Set(exclude.map(Number));
  return new Promise((resolve, reject) => {
    const tryPort = (port) => {
      while (port <= endPort && excluded.has(port)) port += 1;
      if (port > endPort) {
        reject(new Error(`No available port in range ${startPort}-${endPort} (excluding ${[...excluded].join(', ')})`));
        return;
      }
      const server = net.createServer();
      server.once('error', () => tryPort(port + 1));
      server.once('listening', () => server.close(() => resolve(port)));
      server.listen(port, host);
    };
    tryPort(startPort);
  });
}

function waitForHealth(url, {
  timeoutMs = HEALTH_TIMEOUT_MS,
  intervalMs = HEALTH_INTERVAL_MS,
  requestTimeoutMs = HEALTH_REQUEST_TIMEOUT_MS,
  shouldAbort = null,
} = {}) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    let settled = false;
    let retryTimer = null;
    let activeRequest = null;
    const finish = (err, result) => {
      if (settled) return;
      settled = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (activeRequest && !activeRequest.destroyed) activeRequest.destroy();
      if (err) reject(err);
      else resolve(result);
    };
    const scheduleNext = () => {
      if (!settled) retryTimer = setTimeout(attempt, intervalMs);
    };
    const attempt = () => {
      if (settled) return;
      if (typeof shouldAbort === 'function' && shouldAbort()) {
        finish(new Error('Health check aborted'));
        return;
      }
      const elapsed = Date.now() - start;
      if (elapsed > timeoutMs) {
        finish(new Error(`Health check timeout after ${elapsed}ms`));
        return;
      }
      activeRequest = http.get(url, (res) => {
        res.resume();
        if (res.statusCode === 200) {
          finish(null, { elapsedMs: Date.now() - start });
          return;
        }
        scheduleNext();
      });
      activeRequest.setTimeout(requestTimeoutMs, () => {
        activeRequest.destroy(new Error(`Health probe request timeout after ${requestTimeoutMs}ms`));
      });
      activeRequest.on('error', () => scheduleNext());
    };
    attempt();
  });
}

function shouldConfirmQuit(hasRunningAnalysis, skipConfirm = false) {
  if (skipConfirm) return false;
  return Boolean(hasRunningAnalysis);
}

function startBackend({ port, envFile, dbPath, logDir, host = null }) {
  const bindHost = normalizeBackendBindHost(host) || DEFAULT_BIND_HOST;
  const env = buildBackendEnvironment({ envFile, dbPath, logDir, port, host: bindHost });
  const args = buildBackendArgs({ host: bindHost, port });
  const backendPath = resolveBackendPath();
  let proc;
  if (backendPath) {
    if (!fs.existsSync(backendPath)) {
      throw new Error(`Backend executable not found: ${backendPath}`);
    }
    proc = spawn(backendPath, args, {
      env,
      cwd: path.dirname(backendPath),
      stdio: 'pipe',
      windowsHide: true,
    });
  } else {
    const appRoot = resolveAppRoot();
    const pythonPath = resolvePythonPath(appRoot);
    const scriptPath = path.join(appRoot, 'main.py');
    proc = spawn(pythonPath, ['-X', 'utf8', scriptPath, ...args], {
      env,
      cwd: appRoot,
      stdio: 'pipe',
      windowsHide: true,
    });
  }
  return proc;
}

function stopBackendProcess(processRef) {
  if (!processRef) return Promise.resolve();
  if (processRef.exitCode !== null || processRef.signalCode) {
    return Promise.resolve();
  }
  if (process.platform === 'win32') {
    spawn('taskkill', ['/PID', String(processRef.pid), '/T', '/F'], { windowsHide: true });
    return new Promise((resolve) => {
      const timer = setTimeout(resolve, 2000);
      processRef.once('exit', () => { clearTimeout(timer); resolve(); });
    });
  }
  processRef.kill('SIGTERM');
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      if (!processRef.killed) {
        try { processRef.kill('SIGKILL'); } catch (_e) { /* noop */ }
      }
      resolve();
    }, 3000);
    processRef.once('exit', () => { clearTimeout(timer); resolve(); });
  });
}

function bootstrap() {
  const { app, BrowserWindow, Tray, Menu, ipcMain, dialog, shell, nativeImage, safeStorage } = loadElectron();
  if (safeStorage) keyVault.setSafeStorage(safeStorage);

  let mainWindow = null;
  let backendProcess = null;
  let backendPort = null;
  let backendUrl = '';
  let logFilePath = null;
  let hasActiveAnalysis = false;
  let tray = null;
  let quitConfirmed = false;
  let firstCloseHintShown = false;
  let searxngState = { baseUrls: null };
  let remoteState = { enabled: false };
  let cloudflaredChild = null;

  function ensureDirectory(dirPath) {
    if (!fs.existsSync(dirPath)) fs.mkdirSync(dirPath, { recursive: true });
  }

  function logLine(message) {
    const line = `[${new Date().toISOString()}] ${message}\n`;
    try {
      if (logFilePath) fs.appendFileSync(logFilePath, line, 'utf-8');
    } catch (_e) { /* noop */ }
    console.log(line.trim());
  }

  function resolvePaths() {
    const dataDir = resolveDataDir();
    ensureDirectory(dataDir);
    ensureDirectory(path.join(dataDir, 'logs'));
    ensureDirectory(path.join(dataDir, 'data'));
    const envFile = path.join(dataDir, '.env');
    ensureEnvFile(envFile);
    return {
      dataDir,
      envFile,
      dbPath: path.join(dataDir, 'data', 'stock_analysis.db'),
      logDir: path.join(dataDir, 'logs'),
    };
  }

  function isInternalUrl(navigationUrl) {
    try {
      const parsed = new URL(navigationUrl);
      if (parsed.protocol === 'file:') return true;
      return backendUrl !== '' && parsed.origin === new URL(backendUrl).origin;
    } catch (_e) {
      return false;
    }
  }

  async function loadErrorPage(message) {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    const errorFile = path.join(__dirname, 'renderer', 'error.html');
    try {
      await mainWindow.loadFile(errorFile, { query: { message: String(message || ''), log: logFilePath || '' } });
    } catch (e) {
      logLine(`Failed to load error page: ${e}`);
    }
  }

  async function launchBackendAndShow() {
    const paths = resolvePaths();
    logFilePath = paths.logDir ? path.join(paths.logDir, 'desktop.log') : null;
    logLine('Desktop app starting');
    try {
      backendPort = await findAvailablePort();
    } catch (e) {
      logLine(`Port selection failed: ${e.message}`);
      await loadErrorPage(`无法分配后端端口：${e.message}`);
      return;
    }
    const bindHost = DEFAULT_BIND_HOST;
    backendUrl = buildBackendUrl(resolveConnectHost(bindHost), backendPort);
    try {
      backendProcess = startBackend({
        port: backendPort,
        envFile: paths.envFile,
        dbPath: paths.dbPath,
        logDir: paths.logDir,
        host: bindHost,
        secrets: keyVault.loadSecrets(paths.dataDir),
        searxng: searxngState,
        remote: remoteState,
      });
    } catch (e) {
      logLine(`Backend start failed: ${e.message}`);
      await loadErrorPage(`后端启动失败：${e.message}\n请确认已安装 Python 或完整客户端包。`);
      return;
    }
    if (backendProcess) {
      backendProcess.on('error', (err) => logLine(`[backend] failed to start: ${err.message}`));
      backendProcess.stdout?.on('data', (d) => logLine(`[backend] ${String(d).trim()}`));
      backendProcess.stderr?.on('data', (d) => logLine(`[backend] ${String(d).trim()}`));
      backendProcess.on('exit', (code, signal) => logLine(`[backend] exited code=${code} signal=${signal || 'none'}`));
    }
    const loadingFile = path.join(__dirname, 'renderer', 'loading.html');
    try {
      await mainWindow.loadFile(loadingFile);
    } catch (e) {
      logLine(`Failed to load loading page: ${e}`);
    }
    try {
      await waitForHealth(`${backendUrl}${HEALTH_PATH}`, { timeoutMs: HEALTH_TIMEOUT_MS });
      if (!mainWindow.isDestroyed()) {
        await mainWindow.loadURL(`${backendUrl}?desktop_version=${app.getVersion() || 'unknown'}&cache_bust=${Date.now()}`);
      }
    } catch (e) {
      logLine(`Backend health failed: ${e.message}`);
      await loadErrorPage(`后端未能在限定时间内就绪：${e.message}\n日志：${logFilePath || 'n/a'}`);
    }
  }

  function createWindow() {
    mainWindow = new BrowserWindow({
      width: 1280,
      height: 860,
      minWidth: 960,
      minHeight: 640,
      show: false,
      backgroundColor: '#08080c',
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        preload: path.join(__dirname, 'preload.js'),
      },
    });
    if (mainWindow) {
      mainWindow.once('ready-to-show', () => mainWindow?.show());
      mainWindow.webContents.on('will-navigate', (event, url) => {
        if (!isInternalUrl(url)) event.preventDefault();
      });
      mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        if (url.startsWith('http://') || url.startsWith('https://')) {
          shell.openExternal(url);
        }
        return { action: 'deny' };
      });
      mainWindow.on('close', (event) => {
        if (!quitConfirmed) {
          event.preventDefault();
          if (mainWindow) mainWindow.hide();
          if (!firstCloseHintShown) {
            firstCloseHintShown = true;
            if (tray) {
              dialog.showMessageBox(mainWindow || undefined, {
                type: 'info',
                title: '已最小化到托盘',
                message: '客户端已最小化到系统托盘，引擎仍在后台运行。右键托盘图标可退出。',
                buttons: ['知道了'],
              }).catch(() => {});
            }
          }
        }
      });
    }
  }

  function createTray() {
    try {
      const iconPath = path.join(__dirname, 'renderer', 'icon.png');
      let icon = nativeImage.createFromPath(iconPath);
      if (!icon || icon.isEmpty()) icon = nativeImage.createEmpty();
      tray = new Tray(icon);
      const contextMenu = Menu.buildFromTemplate([
        { label: '显示主窗口', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } } },
        { label: '打开数据目录', click: () => { shell.openPath(resolveDataDir()); } },
        { type: 'separator' },
        { label: '退出', click: () => { quitConfirmed = true; app.quit(); } },
      ]);
      tray.setToolTip('Daily Stock Analysis');
      tray.setContextMenu(contextMenu);
      tray.on('click', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
    } catch (e) {
      logLine(`Tray init skipped: ${e.message}`);
    }
  }

  function registerIpc() {
    ipcMain.handle('dsa:getBackendStatus', () => ({
      running: Boolean(backendProcess && backendProcess.exitCode === null && !backendProcess.signalCode),
      port: backendPort,
      url: backendUrl,
    }));
    ipcMain.handle('dsa:restartBackend', async () => {
      if (backendProcess) await stopBackendProcess(backendProcess);
      backendProcess = null;
      await launchBackendAndShow();
      return { ok: true };
    });
    ipcMain.handle('dsa:getAppVersion', () => app.getVersion());
    ipcMain.handle('dsa:openDataDir', () => { shell.openPath(resolveDataDir()); return true; });
    ipcMain.handle('dsa:quitApp', () => { quitConfirmed = true; app.quit(); return true; });
    ipcMain.handle('dsa:setActiveAnalysis', (_e, value) => { hasActiveAnalysis = Boolean(value); return true; });
    ipcMain.handle('dsa:saveSecrets', (_e, secrets) => {
      keyVault.saveSecrets(resolveDataDir(), secrets || {});
      return true;
    });
    ipcMain.handle('dsa:loadSecrets', () => keyVault.loadSecrets(resolveDataDir()));
    ipcMain.handle('dsa:hasSecrets', () => keyVault.hasSecrets(resolveDataDir()));
    ipcMain.handle('dsa:searxngStatus', async () => {
      const dockerAvailable = await searxng.checkDockerAvailable();
      if (!dockerAvailable) {
        return { dockerAvailable, containerRunning: false, healthy: false, status: 'docker_missing', baseUrl: searxng.SEARXNG_BASE_URL };
      }
      const containerRunning = await searxng.isContainerRunning();
      let healthy = false;
      if (containerRunning) {
        healthy = (await searxng.probeHealth()).ok;
      }
      const status = searxng.evaluateSearxngStatus({ dockerAvailable, containerRunning, healthy });
      return { dockerAvailable, containerRunning, healthy, status, baseUrl: searxng.SEARXNG_BASE_URL };
    });
    ipcMain.handle('dsa:searxngStart', async () => {
      const dir = searxng.resolveSearxngDir();
      await searxng.runCompose(dir, 'up', ['-d']);
      let healthy = false;
      for (let i = 0; i < 10 && !healthy; i += 1) {
        healthy = (await searxng.probeHealth()).ok;
        if (!healthy) await new Promise((r) => setTimeout(r, 2000));
      }
      if (healthy) searxngState.baseUrls = searxng.SEARXNG_BASE_URL;
      const status = searxng.evaluateSearxngStatus({ dockerAvailable: true, containerRunning: true, healthy });
      return { ok: healthy, healthy, status, baseUrl: searxng.SEARXNG_BASE_URL };
    });
    ipcMain.handle('dsa:searxngStop', async () => {
      const dir = searxng.resolveSearxngDir();
      await searxng.runCompose(dir, 'down');
      searxngState.baseUrls = null;
      return { ok: true };
    });
    ipcMain.handle('dsa:remoteStatus', () => ({
      enabled: remoteState.enabled,
      lanAddresses: remote.enumerateLanAddresses(),
      baseUrl: remoteState.enabled ? `http://0.0.0.0:${backendPort}` : null,
    }));
    ipcMain.handle('dsa:setRemoteMode', (_e, enabled) => {
      remoteState.enabled = Boolean(enabled);
      return { ok: true, needsRestart: true };
    });
    ipcMain.handle('dsa:getLanAddresses', () => remote.enumerateLanAddresses());
    ipcMain.handle('dsa:cloudflaredStart', async () => {
      const binPath = remote.cloudflaredBinPath(resolveDataDir());
      if (!remote.cloudflaredExists(binPath)) {
        return { ok: false, needsDownload: true };
      }
      if (cloudflaredChild) {
        try { cloudflaredChild.kill(); } catch { /* ignore */ }
        cloudflaredChild = null;
      }
      const child = remote.runCloudflared(binPath, backendPort);
      cloudflaredChild = child;
      let url = null;
      let stdout = '';
      child.stdout?.on('data', (d) => {
        stdout += String(d);
        const found = remote.parseCloudflaredUrl(stdout);
        if (found) url = found;
      });
      // Resolve once we have the URL or the process exits.
      const result = await new Promise((resolve) => {
        const timer = setTimeout(() => resolve({ ok: Boolean(url), url }), 15000);
        child.on('exit', () => { clearTimeout(timer); resolve({ ok: Boolean(url), url }); });
      });
      if (!result.ok) {
        try { child.kill(); } catch { /* ignore */ }
        cloudflaredChild = null;
      }
      return result;
    });
    ipcMain.handle('dsa:cloudflaredStop', () => {
      if (cloudflaredChild) {
        try { cloudflaredChild.kill(); } catch { /* ignore */ }
        cloudflaredChild = null;
      }
      return { ok: true };
    });
  }

  app.whenReady().then(async () => {
    if (!app.requestSingleInstanceLock()) {
      app.quit();
      return;
    }
    app.on('second-instance', () => {
      if (mainWindow) {
        if (mainWindow.isMinimized()) mainWindow.restore();
        mainWindow.show();
        mainWindow.focus();
      }
    });
    registerIpc();
    createWindow();
    createTray();
    await launchBackendAndShow();
  });

  app.on('before-quit', async (event) => {
    if (shouldConfirmQuit(hasActiveAnalysis, quitConfirmed)) {
      event.preventDefault();
      const result = await dialog.showMessageBox(mainWindow || undefined, {
        type: 'question',
        buttons: ['稍后', '强制退出'],
        defaultId: 0,
        cancelId: 0,
        title: '分析任务进行中',
        message: '当前有分析任务正在运行，退出会中断任务。确定要退出吗？',
      }).catch(() => ({ response: 0 }));
      if (result.response === 1) {
        quitConfirmed = true;
        app.quit();
      }
    }
  });

  app.on('quit', () => {
    if (backendProcess) {
      stopBackendProcess(backendProcess).catch(() => {});
      backendProcess = null;
    }
  });
}

if (process.versions && process.versions.electron) {
  bootstrap();
}

module.exports = {
  SEARXNG_RESERVED_PORT,
  DEFAULT_BIND_HOST,
  normalizeBackendHost,
  normalizeBackendBindHost,
  resolveConnectHost,
  buildBackendArgs,
  buildBackendEnvironment,
  buildBackendUrl,
  findAvailablePort,
  waitForHealth,
  shouldConfirmQuit,
  startBackend,
  stopBackendProcess,
};
