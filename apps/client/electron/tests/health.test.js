'use strict';

const test = require('node:test');
const assert = require('node:assert');
const http = require('http');
const { waitForHealth } = require('../main.js');

function startServer(statusCode) {
  const server = http.createServer((req, res) => { res.statusCode = statusCode; res.end('ok'); });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function urlOf(server) {
  const { port } = server.address();
  return `http://127.0.0.1:${port}/api/health`;
}

test('resolves when backend returns 200', async () => {
  const server = await startServer(200);
  try {
    const result = await waitForHealth(urlOf(server), { timeoutMs: 3000, intervalMs: 50, requestTimeoutMs: 500 });
    assert.ok(result && typeof result.elapsedMs === 'number');
  } finally {
    server.close();
  }
});

test('rejects on timeout when backend never healthy', async () => {
  const server = await startServer(503);
  try {
    await assert.rejects(
      () => waitForHealth(urlOf(server), { timeoutMs: 300, intervalMs: 50, requestTimeoutMs: 100 }),
      /timeout/,
    );
  } finally {
    server.close();
  }
});
