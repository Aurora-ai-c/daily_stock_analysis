const { test } = require('node:test');
const assert = require('node:assert');
const path = require('path');
const remote = require('../remote');

test('isPrivateIpv4 classifies LAN ranges', () => {
  assert.strictEqual(remote.isPrivateIpv4('10.0.0.5'), true);
  assert.strictEqual(remote.isPrivateIpv4('172.16.0.1'), true);
  assert.strictEqual(remote.isPrivateIpv4('172.31.255.255'), true);
  assert.strictEqual(remote.isPrivateIpv4('192.168.1.10'), true);
  assert.strictEqual(remote.isPrivateIpv4('127.0.0.1'), false);
  assert.strictEqual(remote.isPrivateIpv4('8.8.8.8'), false);
  assert.strictEqual(remote.isPrivateIpv4('172.15.0.1'), false);
  assert.strictEqual(remote.isPrivateIpv4('172.32.0.1'), false);
  assert.strictEqual(remote.isPrivateIpv4('not-an-ip'), false);
  assert.strictEqual(remote.isPrivateIpv4('1.2.3'), false);
});

test('enumerateLanAddresses returns an array', () => {
  assert.ok(Array.isArray(remote.enumerateLanAddresses()));
});

test('resolveRemoteEnv forces auth when remote is enabled', () => {
  assert.deepStrictEqual(remote.resolveRemoteEnv({ enabled: true, port: 8000 }), {
    WEBUI_HOST: '0.0.0.0',
    ADMIN_AUTH_ENABLED: 'true',
    WEBUI_PORT: '8000',
  });
  assert.deepStrictEqual(remote.resolveRemoteEnv({ enabled: false }), {
    WEBUI_HOST: '127.0.0.1',
    ADMIN_AUTH_ENABLED: 'false',
  });
});

test('parseCloudflaredUrl extracts the tunnel URL', () => {
  const stdout = '2024-01-01 INFO ...\nyour free tunnel is available at: https://abc123.trycloudflare.com\n';
  assert.strictEqual(remote.parseCloudflaredUrl(stdout), 'https://abc123.trycloudflare.com');
  assert.strictEqual(remote.parseCloudflaredUrl('no url here'), null);
  assert.strictEqual(remote.parseCloudflaredUrl(''), null);
});

test('cloudflaredBinPath targets the app data bin dir', () => {
  const p = remote.cloudflaredBinPath('C:\\Users\\x\\AppData\\DSA');
  assert.ok(p.startsWith('C:\\Users\\x\\AppData\\DSA'));
  assert.ok(p.endsWith(process.platform === 'win32' ? 'cloudflared.exe' : 'cloudflared'));
});
