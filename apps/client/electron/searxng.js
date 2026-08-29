const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const https = require('https');

const SEARXNG_PORT = 8080;
const SEARXNG_HOST = '127.0.0.1';
const SEARXNG_BASE_URL = `http://${SEARXNG_HOST}:${SEARXNG_PORT}`;
const CONTAINER_NAME = 'dsa-searxng';

function resolveSearxngDir() {
  // Lazy require keeps this module importable in node --test.
  // eslint-disable-next-line global-require
  const { app } = require('electron');
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'searxng');
  }
  return path.resolve(__dirname, '..', '..', 'searxng');
}

// Pure: build the docker compose invocation. `cwd` must point at the directory
// holding docker-compose.searxng.yml so the relative `./settings.yml` mount
// resolves (compose resolves mounts against the compose file's directory).
function buildComposeCommand(dir, action, extraArgs = []) {
  return {
    command: 'docker',
    args: ['compose', '-f', path.join(dir, 'docker-compose.searxng.yml'), action, ...extraArgs],
    cwd: dir,
  };
}

function runCompose(dir, action, extraArgs = [], timeoutMs = 180000) {
  const { command, args, cwd } = buildComposeCommand(dir, action, extraArgs);
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, windowsHide: true });
    let stderr = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try { child.kill('SIGKILL'); } catch { /* ignore */ }
      resolve({ code: null, stderr, timedOut: true });
    }, timeoutMs);
    child.stderr?.on('data', (d) => { stderr += String(d); });
    child.on('error', (e) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(e);
    });
    child.on('close', (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code, stderr });
    });
  });
}

function checkDockerAvailable() {
  return new Promise((resolve) => {
    const child = spawn('docker', ['info'], { windowsHide: true });
    child.on('error', () => resolve(false));
    child.on('close', (code) => resolve(code === 0));
  });
}

function isContainerRunning() {
  return new Promise((resolve) => {
    const child = spawn('docker', ['ps', '--filter', `name=${CONTAINER_NAME}`, '--format', '{{.Names}}'], { windowsHide: true });
    let out = '';
    child.on('error', () => resolve(false));
    child.stdout?.on('data', (d) => { out += String(d); });
    child.on('close', () => resolve(out.split('\n').some((line) => line.trim() === CONTAINER_NAME)));
  });
}

function parseSearchJson(raw) {
  try {
    const json = JSON.parse(raw);
    return { ok: true, json };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

function probeHealth(baseUrl = SEARXNG_BASE_URL, timeoutMs = 5000) {
  return new Promise((resolve) => {
    let url;
    try {
      url = new URL(`${baseUrl}/search?q=test&format=json`);
    } catch (e) {
      resolve({ ok: false, status: 0, error: e.message });
      return;
    }
    const lib = url.protocol === 'https:' ? https : http;
    const req = lib.get(url, { timeout: timeoutMs }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        const parsed = parseSearchJson(data);
        resolve({ ok: res.statusCode === 200 && parsed.ok, status: res.statusCode, json: parsed.json || null });
      });
    });
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, status: 0, timeout: true }); });
    req.on('error', (e) => resolve({ ok: false, status: 0, error: e.message }));
  });
}

// Pure: assemble the env vars injected into the backend process.
function assembleSearxngEnv(baseUrls) {
  if (!baseUrls) return {};
  const value = Array.isArray(baseUrls) ? baseUrls.join(',') : String(baseUrls);
  return {
    SEARXNG_BASE_URLS: value,
    SEARXNG_PUBLIC_INSTANCES_ENABLED: 'false',
  };
}

// Pure: classify the current SearXNG situation for the UI.
function evaluateSearxngStatus({ dockerAvailable, containerRunning, healthy }) {
  if (!dockerAvailable) return 'docker_missing';
  if (!containerRunning) return 'stopped';
  if (!healthy) return 'unhealthy';
  return 'running';
}

// Pure: decide the next user action from a status.
function nextSearxngAction(status) {
  switch (status) {
    case 'running':
      return 'stop';
    case 'docker_missing':
    case 'stopped':
    case 'unhealthy':
      return 'start';
    default:
      return 'none';
  }
}

module.exports = {
  SEARXNG_PORT,
  SEARXNG_HOST,
  SEARXNG_BASE_URL,
  CONTAINER_NAME,
  resolveSearxngDir,
  buildComposeCommand,
  runCompose,
  checkDockerAvailable,
  isContainerRunning,
  parseSearchJson,
  probeHealth,
  assembleSearxngEnv,
  evaluateSearxngStatus,
  nextSearxngAction,
};
