import assert from 'node:assert/strict';
import test from 'node:test';
import { health, scan } from '../api.mjs';
import { formatDownloadName, normalizePointer, sameCorners, validateImageFile } from '../geometry.mjs';

globalThis.window = { location: { origin: 'http://localhost:8000' } };
globalThis.document = { querySelector: () => null };

const sampleCorners = [[0.1, 0.12], [0.9, 0.1], [0.88, 0.9], [0.12, 0.88]];
const sampleResponse = {
  ok: true,
  corners: sampleCorners,
  corners_source: 'client',
  warped_b64: 'ZmFrZS1qcGVn',
  warped_size: [640, 900],
  warped_mime: 'image/jpeg',
  scan_b64: 'ZmFrZS1wbmc=',
};

function mockJsonResponse(data, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => data };
}

test('image selection accepts JPEG and PNG and rejects unsupported or oversized files', () => {
  assert.equal(validateImageFile({ type: 'image/jpeg', size: 100 }).type, 'image/jpeg');
  assert.equal(validateImageFile({ type: 'image/png', size: 100 }).type, 'image/png');
  assert.throws(() => validateImageFile({ type: 'image/webp', size: 100 }), /JPEG or PNG/);
  assert.throws(() => validateImageFile({ type: 'image/jpeg', size: 9 * 1024 * 1024 }), /8 MB/);
});

test('corner dragging maps viewport coordinates to clamped normalized image coordinates', () => {
  assert.deepEqual(normalizePointer(150, 250, { left: 50, top: 50, width: 200, height: 400 }), [0.5, 0.5]);
  assert.deepEqual(normalizePointer(20, 900, { left: 50, top: 50, width: 200, height: 400 }), [0, 1]);
  assert.throws(() => normalizePointer(10, 10, { left: 0, top: 0, width: 0, height: 0 }), /not ready/);
  assert.equal(sameCorners(sampleCorners, sampleCorners.map(([x, y]) => [x + 0.0001, y])), true);
});

test('scan sends normalized corners and validates the useful image response', async () => {
  const originalFetch = globalThis.fetch;
  let sent;
  globalThis.fetch = async (url, options) => {
    sent = { url, options, body: JSON.parse(options.body) };
    return mockJsonResponse(sampleResponse);
  };
  try {
    const result = await scan({ image: 'aGVsbG8=', corners: sampleCorners });
    assert.equal(result.warped_b64, sampleResponse.warped_b64);
    assert.equal(sent.url, 'http://localhost:8000/scan');
    assert.deepEqual(sent.body.corners, sampleCorners);
    assert.equal(sent.body.image, 'aGVsbG8=');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('scan omits corners for backend detection and rejects incomplete API data', async () => {
  const originalFetch = globalThis.fetch;
  let body;
  globalThis.fetch = async (_url, options) => {
    body = JSON.parse(options.body);
    return mockJsonResponse(sampleResponse);
  };
  try {
    await scan({ image: 'aGVsbG8=' });
    assert.equal(Object.hasOwn(body, 'corners'), false);
    globalThis.fetch = async () => mockJsonResponse({ ok: true, warped_b64: 'abc' });
    await assert.rejects(scan({ image: 'aGVsbG8=' }), /incomplete scan data/);
    await assert.rejects(scan({ image: 'aGVsbG8=', corners: [[2, 2]] }), /four normalized points/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('health parses service response and reports HTTP errors clearly', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => mockJsonResponse({ ok: true, service: 'scanly' });
  try {
    assert.equal((await health()).service, 'scanly');
    globalThis.fetch = async () => mockJsonResponse({ detail: 'image too large' }, 413);
    await assert.rejects(health(), /image too large/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('API reports network, timeout, and malformed JSON failures without image data', async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new TypeError('socket closed'); };
    await assert.rejects(health(), /Could not connect/);
    globalThis.fetch = async (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    });
    await assert.rejects(scan({ image: 'opaque', timeoutMs: 5 }), /took too long/);
    globalThis.fetch = async () => ({ ok: true, json: async () => { throw new SyntaxError('bad json'); } });
    await assert.rejects(health(), /unreadable response/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('download filename uses local date and time in the requested format', () => {
  assert.equal(formatDownloadName(new Date(2026, 9, 3, 9, 7)), 'Scan_2026-10-03_09-07.jpg');
});
