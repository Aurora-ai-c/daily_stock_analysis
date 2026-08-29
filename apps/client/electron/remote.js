const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const https = require('https');
const crypto = require('crypto');
const fs = require('fs');

const DEFAULT_CLOUDFLARED_URL = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe';

function isPrivateIpv4(address) {
  if (typeof address !== 'string') return false;
  const parts = address.split('.');
  if (parts.length !== 4) return false;
  const nums = parts.map((p) => Number(p));
  if (nums.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return false;
  const [a, b] = nums;
  if (a === 127) return false; // loopback excluded from LAN
  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  return false;
}

function enumerateLanAddresses() {
  const interfaces = os.networkInterfaces();
  const out = [];
  for (const name of Object.keys(interfaces)) {
    for (const info of interfaces[name] || []) {
      if (info.family === 'IPv4' && !info.internal && isPrivateIpv4(info.address)) {
        out.push({ interface: name, address: info.address });
      }
    }
  }
  return out;
}

// Pure: derive the backend env overrides for remote vs local mode.
// Remote mode ALWAYS forces ADMIN_AUTH_ENABLED=true (the user cannot open
// 0.0.0.0 without auth).
function resolveRemoteEnv({ enabled, port } = {}) {
  if (enabled) {
    const env = { WEBUI_HOST: '0.0.0.0', ADMIN_AUTH_ENABLED: 'true' };
    if (Number.isInteger(port) && port >= 1 && port <= 65535) env.WEBUI_PORT = String(port);
    return env;
  }
  return { WEBUI_HOST: '127.0.0.1', ADMIN_AUTH_ENABLED: 'false' };
}

// Pure: extract the public tunnel URL from cloudflared stdout.
function parseCloudflaredUrl(stdout) {
  if (!stdout) return null;
  const match = String(stdout).match(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/i);
  return match ? match[0] : null;
}

function cloudflaredBinPath(dataDir) {
  const base = process.platform === 'win32' ? 'cloudflared.exe' : 'cloudflared';
  return path.join(dataDir, 'bin', base);
}

function buildCloudflaredArgs(port) {
  return ['tunnel', '--url', `http://127.0.0.1:${port}`, '--no-autoupdate'];
}

function runCloudflared(binPath, port) {
  return spawn(binPath, buildCloudflaredArgs(port), { windowsHide: true });
}

// Download a pinned cloudflared build with sha256 verification (best effort).
function downloadCloudflared(binPath, url = DEFAULT_CLOUDFLARED_URL, expectedSha256 = null) {
  return new Promise((resolve, reject) => {
    fs.mkdirSync(path.dirname(binPath), { recursive: true });
    const file = fs.createWriteStream(binPath);
    const req = https.get(url, (res) => {
      if (res.statusCode !== 200) {
        reject(new Error(`download failed: ${res.statusCode}`));
        return;
      }
      const hash = crypto.createHash('sha256');
      res.on('data', (chunk) => { file.write(chunk); hash.update(chunk); });
      res.on('end', () => {
        file.end(() => {
          if (expectedSha256 && hash.digest('hex') !== expectedSha256) {
            fs.unlinkSync(binPath);
            reject(new Error('sha256 mismatch'));
            return;
          }
          resolve(binPath);
        });
      });
    });
    req.on('error', (e) => reject(e));
  });
}

function cloudflaredExists(binPath) {
  return fs.existsSync(binPath);
}

module.exports = {
  DEFAULT_CLOUDFLARED_URL,
  isPrivateIpv4,
  enumerateLanAddresses,
  resolveRemoteEnv,
  parseCloudflaredUrl,
  cloudflaredBinPath,
  buildCloudflaredArgs,
  runCloudflared,
  downloadCloudflared,
  cloudflaredExists,
};
