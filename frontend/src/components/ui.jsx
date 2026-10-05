import React from 'react';
import { humanise } from '../lib/format';

/* ------------------------------------------------------------------ */
/* Status badges                                                       */
/* ------------------------------------------------------------------ */

/**
 * Maps a backend value to one of four semantic tones. Colour is only ever used
 * to carry meaning: success, warning, critical, neutral.
 */
const TONE = {
  // reconciliation status
  matched: 'ok',
  partial: 'warn',
  flag_for_review: 'warn',
  unmatched: 'bad',
  // severity
  high: 'bad',
  medium: 'warn',
  low: 'neutral',
  // anomaly lifecycle
  OPEN: 'warn',
  RESOLVED: 'ok',
  IGNORED: 'neutral',
  // settlement window lifecycle
  SETTLED: 'ok',
  CLOSED: 'info',
  PENDING_SETTLEMENT: 'warn',
  OPEN_WINDOW: 'info',
  ABORTED: 'bad',
  ABORTING: 'bad',
  // run / ledger status
  success: 'ok',
  running: 'info',
  failed: 'bad',
  RECONCILED: 'ok',
  PARTIALLY_RECONCILED: 'warn',
  UNRECONCILED: 'neutral',
  CLEARED: 'ok',
  // transfer state
  COMMITTED: 'ok',
  RESERVED: 'warn',
  RECEIVED: 'info',
  SUCCESS: 'ok',
  PENDING: 'warn',
  FAILED: 'bad'
};

export function Badge({ value, tone, label }) {
  const resolved = tone || TONE[value] || TONE[String(value).toUpperCase()] || 'neutral';
  return <span className={`badge badge--${resolved}`}>{label ?? humanise(value)}</span>;
}

/** Small coloured dot plus text, for health rows. */
export function StatusDot({ ok, label, warn }) {
  const tone = ok ? 'ok' : warn ? 'warn' : 'bad';
  return (
    <span className="status-dot-wrap">
      <span className={`status-dot status-dot--${tone}`} aria-hidden="true" />
      {label}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Layout                                                             */
/* ------------------------------------------------------------------ */

export function Card({ title, subtitle, actions, children, className = '', dense }) {
  return (
    <section className={`card ${dense ? 'card--dense' : ''} ${className}`}>
      {(title || actions) && (
        <header className="card__head">
          <div>
            {title && <h2 className="card__title">{title}</h2>}
            {subtitle && <p className="card__subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="card__actions">{actions}</div>}
        </header>
      )}
      <div className="card__body">{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="page-header">
      <div>
        <h1 className="page-header__title">{title}</h1>
        {subtitle && <p className="page-header__subtitle">{subtitle}</p>}
      </div>
      {children && <div className="page-header__aside">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* KPI tile                                                           */
/* ------------------------------------------------------------------ */

/**
 * Compact metric tile. The value is the visual priority; the label and any
 * supporting note are deliberately quiet.
 */
export function Kpi({ label, value, note, tone, onClick }) {
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      className={`kpi ${tone ? `kpi--${tone}` : ''} ${onClick ? 'kpi--clickable' : ''}`}
      onClick={onClick}
      type={onClick ? 'button' : undefined}
    >
      <span className="kpi__label">{label}</span>
      <strong className="kpi__value">{value}</strong>
      {note && <span className="kpi__note">{note}</span>}
    </Tag>
  );
}

/* ------------------------------------------------------------------ */
/* Controls                                                           */
/* ------------------------------------------------------------------ */

export function Button({ children, variant = 'default', size, ...rest }) {
  return (
    <button
      type="button"
      className={`btn btn--${variant} ${size ? `btn--${size}` : ''}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Field({ label, children, width }) {
  return (
    <label className="field" style={width ? { width } : undefined}>
      <span className="field__label">{label}</span>
      {children}
    </label>
  );
}

export function Select({ value, onChange, options, placeholder = 'All', ...rest }) {
  return (
    <select
      className="input"
      value={value}
      onChange={e => onChange(e.target.value)}
      {...rest}
    >
      <option value="">{placeholder}</option>
      {options.map(opt =>
        typeof opt === 'string' ? (
          <option key={opt} value={opt}>
            {humanise(opt)}
          </option>
        ) : (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        )
      )}
    </select>
  );
}

export function TextInput({ value, onChange, ...rest }) {
  return (
    <input
      className="input"
      value={value}
      onChange={e => onChange(e.target.value)}
      {...rest}
    />
  );
}

/** Row of filter controls above a table. */
export function FilterBar({ children, onReset }) {
  return (
    <div className="filter-bar">
      {children}
      {onReset && (
        <Button variant="quiet" size="sm" onClick={onReset}>
          Reset
        </Button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Feedback                                                           */
/* ------------------------------------------------------------------ */

export function EmptyState({ title, hint }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {hint && <p className="empty__hint">{hint}</p>}
    </div>
  );
}

export function Loading({ label = 'Loading…' }) {
  return <div className="loading">{label}</div>;
}

export function Notice({ kind = 'info', children, onDismiss }) {
  if (!children) return null;
  return (
    <div className={`notice notice--${kind}`} role="status">
      <span>{children}</span>
      {onDismiss && (
        <button type="button" className="notice__close" onClick={onDismiss} aria-label="Dismiss">
          ✕
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Pagination                                                         */
/* ------------------------------------------------------------------ */

export function Pagination({ total, limit, offset, onChange }) {
  if (!total) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);

  return (
    <div className="pagination">
      <span className="pagination__count">
        {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}
      </span>
      <div className="pagination__controls">
        <Button
          size="sm"
          variant="quiet"
          disabled={offset === 0}
          onClick={() => onChange(0)}
        >
          First
        </Button>
        <Button
          size="sm"
          variant="quiet"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <span className="pagination__page">
          Page {page} of {pages}
        </span>
        <Button
          size="sm"
          variant="quiet"
          disabled={offset + limit >= total}
          onClick={() => onChange(offset + limit)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
