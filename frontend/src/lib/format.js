/**
 * Display formatting for financial data.
 *
 * Money is always shown with its currency, and grouped the way that currency's
 * locale groups it: INR uses the lakh/crore convention (24,82,450.00) while USD
 * and EUR use thousands. The previous dashboard formatted every figure as INR
 * regardless of the backend's base currency, which mislabelled USD values with
 * a rupee symbol.
 */

const LOCALE_BY_CURRENCY = {
  INR: 'en-IN',
  USD: 'en-US',
  EUR: 'de-DE',
  GBP: 'en-GB',
  KES: 'en-KE',
  TZS: 'en-TZ',
  UGX: 'en-UG',
  NGN: 'en-NG',
  ZAR: 'en-ZA',
  GHS: 'en-GH',
  XOF: 'fr-SN',
  RWF: 'en-RW'
};

const localeFor = currency => LOCALE_BY_CURRENCY[(currency || '').toUpperCase()] || 'en-US';

const formatterCache = new Map();

const getFormatter = (currency, options) => {
  const code = (currency || 'USD').toUpperCase();
  const key = `${code}|${JSON.stringify(options)}`;
  if (!formatterCache.has(key)) {
    let formatter;
    try {
      formatter = new Intl.NumberFormat(localeFor(code), {
        style: 'currency',
        currency: code,
        ...options
      });
    } catch {
      // Unknown currency code: fall back to a plain decimal and append the code.
      formatter = new Intl.NumberFormat('en-US', options);
    }
    formatterCache.set(key, formatter);
  }
  return formatterCache.get(key);
};

/** Full precision money, e.g. ₹24,82,450.00 or $7,889,382.38 */
export function money(amount, currency = 'USD') {
  if (amount === null || amount === undefined || Number.isNaN(Number(amount))) return '—';
  return getFormatter(currency, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(
    Number(amount)
  );
}

/** Money without decimals, for dense table columns and KPI tiles. */
export function moneyShort(amount, currency = 'USD') {
  if (amount === null || amount === undefined || Number.isNaN(Number(amount))) return '—';
  return getFormatter(currency, { minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(
    Number(amount)
  );
}

/**
 * Abbreviated money for headline figures, e.g. $7.89M.
 * Uses the currency's own symbol rather than inventing one.
 */
export function moneyCompact(amount, currency = 'USD') {
  if (amount === null || amount === undefined || Number.isNaN(Number(amount))) return '—';
  const code = (currency || 'USD').toUpperCase();
  try {
    return new Intl.NumberFormat(localeFor(code), {
      style: 'currency',
      currency: code,
      notation: 'compact',
      maximumFractionDigits: 2
    }).format(Number(amount));
  } catch {
    return moneyShort(amount, code);
  }
}

export function number(value, maximumFractionDigits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits }).format(Number(value));
}

export function percent(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(digits)}%`;
}

/** Risk scores are stored 0–1; operations staff read them as 0–100. */
export function riskScore(value) {
  if (value === null || value === undefined) return '—';
  return Math.round(Number(value) * 100);
}

const pad = n => String(n).padStart(2, '0');

const toDate = value => {
  if (!value) return null;
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

/** 2026-10-05 14:32 — one unambiguous format everywhere. */
export function dateTime(value) {
  const d = toDate(value);
  if (!d) return '—';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

export function dateOnly(value) {
  const d = toDate(value);
  if (!d) return '—';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function timeOnly(value) {
  const d = toDate(value);
  if (!d) return '—';
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** "2 minutes ago" for sync indicators. */
export function relativeTime(value) {
  const d = toDate(value);
  if (!d) return '—';
  const seconds = Math.round((Date.now() - d.getTime()) / 1000);
  if (seconds < 0) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const value = Number(seconds);
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  if (value < 60) return `${value.toFixed(2)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m ${Math.round(value % 60)}s`;
}

/** matched -> Matched, flag_for_review -> Flag for review */
export function humanise(value) {
  if (!value) return '—';
  const text = String(value).replace(/_/g, ' ').toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}
