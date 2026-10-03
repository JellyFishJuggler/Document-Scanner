const DEFAULT_TIMEOUT_MS = 90_000;

export function getApiBaseUrl() {
  const configured = document.querySelector('meta[name="scanly-api-base"]')?.content?.trim();
  return (configured || window.SCANLY_API_BASE_URL || window.location.origin).replace(/\/$/, '');
}

async function fetchJson(path, options = {}) {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    let response;
    try {
      response = await fetch(`${getApiBaseUrl()}${path}`, {
        method: options.method ?? 'GET',
        headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      });
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new Error('The request took too long. Check your connection and try again.');
      }
      throw new Error('Could not connect to Scanly. Check your connection and try again.');
    }

    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error('Scanly returned an unreadable response. Please try again.');
    }

    if (!response.ok) {
      const detail = typeof data?.detail === 'string' ? data.detail : `Request failed (${response.status}).`;
      throw new Error(detail);
    }
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

function isNormalizedCorners(corners) {
  return Array.isArray(corners)
    && corners.length === 4
    && corners.every(point => Array.isArray(point)
      && point.length === 2
      && point.every(value => Number.isFinite(value) && value >= 0 && value <= 1));
}

function validateScanResponse(data) {
  if (data?.ok !== true
    || typeof data.warped_b64 !== 'string'
    || data.warped_b64.length === 0
    || data.warped_mime !== 'image/jpeg'
    || !Array.isArray(data.warped_size)
    || data.warped_size.length !== 2
    || !isNormalizedCorners(data.corners)
    || typeof data.corners_source !== 'string'
    || typeof data.scan_b64 !== 'string'
    || data.scan_b64.length === 0) {
    throw new Error('Scanly returned incomplete scan data. Please try again.');
  }
  return data;
}

export async function health() {
  const data = await fetchJson('/health');
  if (data?.ok !== true) throw new Error('Scanly is not available right now.');
  return data;
}

export async function scan({ image, corners, timeoutMs } = {}) {
  if (typeof image !== 'string' || image.length === 0) {
    throw new Error('Choose an image before scanning.');
  }
  if (corners !== undefined && !isNormalizedCorners(corners)) {
    throw new Error('Corner positions must be four normalized points.');
  }

  const body = { image, method: 'otsu', max_side: 2000 };
  if (corners !== undefined) body.corners = corners;
  const data = await fetchJson('/scan', { method: 'POST', body, timeoutMs });
  return validateScanResponse(data);
}
