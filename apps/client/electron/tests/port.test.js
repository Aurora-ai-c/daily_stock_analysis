'use strict';

const test = require('node:test');
const assert = require('node:assert');
const net = require('net');
const { findAvailablePort, SEARXNG_RESERVED_PORT } = require('../main.js');

function occupyPort(port) {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(port, '127.0.0.1', () => resolve(s));
  });
}

test('excludes SearXNG reserved port 8080', async () => {
  const server = await occupyPort(SEARXNG_RESERVED_PORT);
  try {
    const port = await findAvailablePort({ startPort: 8080, endPort: 8090, exclude: [SEARXNG_RESERVED_PORT] });
    assert.notStrictEqual(port, SEARXNG_RESERVED_PORT);
    assert.ok(port >= 8080 && port <= 8090, `port ${port} out of range`);
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test('rejects when entire range is occupied', async () => {
  const servers = [];
  for (let p = 8050; p <= 8052; p += 1) servers.push(await occupyPort(p));
  try {
    await assert.rejects(
      () => findAvailablePort({ startPort: 8050, endPort: 8052, exclude: [] }),
      /No available port/,
    );
  } finally {
    servers.forEach((s) => s.close());
  }
});
