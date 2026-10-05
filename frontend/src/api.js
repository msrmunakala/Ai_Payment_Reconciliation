/**
 * Backend API client.
 *
 * Every call goes through here so authentication and error handling are in one
 * place. The existing contract is preserved exactly: the API key travels in the
 * `X-API-Key` header and both the base URL and the key come from Vite
 * environment variables, falling back to the local development defaults.
 */

export const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const API_KEY = import.meta.env.VITE_API_KEY || 'review2-demo-key';

const authHeaders = () => ({ 'X-API-Key': API_KEY });

/** Error carrying the HTTP status and the backend's `detail`, so callers can show it. */
export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

const buildUrl = (path, params) => {
  const url = new URL(path.startsWith('http') ? path : API_BASE + path);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    if (Array.isArray(value)) {
      value.forEach(v => url.searchParams.append(key, v));
    } else {
      url.searchParams.set(key, value);
    }
  });
  return url.toString();
};

async function request(path, { method = 'GET', params, body, signal } = {}) {
  const headers = authHeaders();
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const response = await fetch(buildUrl(path, params), {
    method,
    headers,
    signal,
    body: body === undefined ? undefined : JSON.stringify(body)
  });

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const detail =
      (payload && typeof payload === 'object' && payload.detail) ||
      (typeof payload === 'string' ? payload : null) ||
      response.statusText;
    throw new ApiError(
      typeof detail === 'string' ? detail : `Request failed (${response.status})`,
      response.status,
      detail
    );
  }
  return payload;
}

/** Download a non-JSON response (CSV export) as a file. */
export async function downloadFile(path, filename, params) {
  const response = await fetch(buildUrl(path, params), { headers: authHeaders() });
  if (!response.ok) throw new ApiError(`Export failed (${response.status})`, response.status);
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(href);
}

/** Paginated endpoints return `{ total, limit, offset, returned, items }`. */
const page = payload =>
  Array.isArray(payload)
    ? { total: payload.length, items: payload, limit: payload.length, offset: 0 }
    : {
        total: payload?.total ?? 0,
        items: payload?.items ?? [],
        limit: payload?.limit ?? 0,
        offset: payload?.offset ?? 0
      };

export const api = {
  // --- meta / health -------------------------------------------------
  root: () => request('/'),
  health: () => request('/health'),
  ready: () => request('/ready'),

  // --- dashboard -----------------------------------------------------
  summary: () => request('/dashboard/summary'),
  runs: (limit = 20) => request('/dashboard/runs', { params: { limit } }),

  // --- reconciliation ------------------------------------------------
  results: params => request('/reconciliation/results', { params }).then(page),
  result: transactionId => request(`/reconciliation/results/${encodeURIComponent(transactionId)}`),
  runReconciliation: params =>
    request('/reconciliation/run', { method: 'POST', params }),
  manualMatch: body => request('/reconciliation/manual-match', { method: 'POST', body }),
  removeManualMatch: transactionId =>
    request(`/reconciliation/manual-match/${encodeURIComponent(transactionId)}`, {
      method: 'DELETE'
    }),

  // --- anomalies -----------------------------------------------------
  anomalies: params => request('/reconciliation/anomalies', { params }).then(page),
  resolveAnomaly: (anomalyId, body) =>
    request(`/reconciliation/anomalies/${anomalyId}/resolve`, { method: 'POST', body }),

  // --- transactions / ledgers ---------------------------------------
  transactions: params => request('/transactions/', { params }).then(page),
  transaction: id => request(`/transactions/${encodeURIComponent(id)}`),
  bankRecords: params => request('/transactions/bank', { params }).then(page),
  erpRecords: params => request('/transactions/erp', { params }).then(page),

  // --- settlement ----------------------------------------------------
  windows: params => request('/settlement/windows', { params }).then(page),
  window: id => request(`/settlement/windows/${encodeURIComponent(id)}`),
  pendingWindows: () => request('/settlement/windows/pending'),
  syncWindows: params => request('/settlement/sync', { method: 'POST', params }),
  reconcileWindow: (id, params) =>
    request(`/settlement/windows/${encodeURIComponent(id)}/reconcile`, {
      method: 'POST',
      params
    }),
  reconcilePendingWindows: params =>
    request('/settlement/windows/reconcile-pending', { method: 'POST', params }),
  settlements: params => request('/settlement/settlements', { params }).then(page),
  syncSettlements: settlementIds =>
    request('/settlement/settlements/sync', {
      method: 'POST',
      params: { settlement_ids: settlementIds }
    }),

  // --- forecasting ---------------------------------------------------
  forecast: (days = 7, refresh = false) =>
    request('/forecast/', { params: { days, refresh } }),
  forecastMetadata: () => request('/forecast/metadata'),
  forecastHistory: (lookbackDays = 90) =>
    request('/forecast/history', { params: { lookback_days: lookbackDays } }),
  runForecast: days => request('/forecast/run', { method: 'POST', params: { days } }),

  // --- fx ------------------------------------------------------------
  fxRates: includeHistory => request('/fx/rates', { params: { include_history: includeHistory } }),
  fxConvert: params => request('/fx/convert', { params }),
  refreshFx: () => request('/fx/refresh', { method: 'POST' }),
  setFxRate: (currency, unitsPerBase) =>
    request(`/fx/rates/${encodeURIComponent(currency)}`, {
      method: 'PUT',
      params: { units_per_base: unitsPerBase }
    }),

  // --- scheduler -----------------------------------------------------
  schedulerStatus: () => request('/scheduler/status'),
  startScheduler: intervalMinutes =>
    request('/scheduler/start', { method: 'POST', params: { interval_minutes: intervalMinutes } }),
  stopScheduler: () => request('/scheduler/stop', { method: 'POST' }),
  pauseScheduler: () => request('/scheduler/pause', { method: 'POST' }),
  resumeScheduler: () => request('/scheduler/resume', { method: 'POST' }),
  setSchedulerInterval: intervalMinutes =>
    request('/scheduler/interval', { method: 'POST', params: { interval_minutes: intervalMinutes } }),
  runSchedulerNow: params => request('/scheduler/run-now', { method: 'POST', params }),

  // --- mojaloop ------------------------------------------------------
  webhookStatus: () => request('/webhooks/status'),
  verifyTransfer: transactionId =>
    request(`/webhooks/transfers/${encodeURIComponent(transactionId)}/verify`),

  // --- demo / reporting ----------------------------------------------
  resetDemo: params => request('/demo/reset', { method: 'POST', params }),
  exportCsv: status =>
    downloadFile('/reports/export.csv', 'reconciliation-report.csv', { status }),
  exportJson: () => downloadFile('/demo/export-json', 'reconciliation-dataset.json', { limit: 5000 })
};
