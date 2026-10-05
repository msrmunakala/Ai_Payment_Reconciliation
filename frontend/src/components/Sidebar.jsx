import React from 'react';

/**
 * Compact primary navigation. Counts are shown only where an operator needs to
 * know there is work waiting (open exceptions, windows awaiting reconciliation).
 */
export const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'reconciliation', label: 'Reconciliation' },
  { id: 'transactions', label: 'Transactions' },
  { id: 'settlement-windows', label: 'Settlement Windows' },
  { id: 'anomalies', label: 'Anomalies' },
  { id: 'forecast', label: 'Forecast' },
  { id: 'fx-rates', label: 'FX Rates' },
  { id: 'run-history', label: 'Run History' },
  { id: 'settings', label: 'Settings' }
];

export function Sidebar({ current, onNavigate, badges = {}, baseCurrency, collapsed, onToggle }) {
  return (
    <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`}>
      <div className="sidebar__brand">
        <span className="sidebar__mark" aria-hidden="true">
          RO
        </span>
        <span className="sidebar__brand-text">
          <strong>Reconciliation Ops</strong>
          <em>Mojaloop settlement</em>
        </span>
      </div>

      <nav className="sidebar__nav" aria-label="Primary">
        {NAV_ITEMS.map(item => {
          const count = badges[item.id];
          return (
            <button
              key={item.id}
              type="button"
              className={`sidebar__link ${current === item.id ? 'is-active' : ''}`}
              onClick={() => onNavigate(item.id)}
              aria-current={current === item.id ? 'page' : undefined}
            >
              <span className="sidebar__link-label">{item.label}</span>
              {count ? <span className="sidebar__count">{count}</span> : null}
            </button>
          );
        })}
      </nav>

      <div className="sidebar__foot">
        <div className="sidebar__base">
          Base currency
          <strong>{baseCurrency || '—'}</strong>
        </div>
        <button type="button" className="sidebar__collapse" onClick={onToggle}>
          {collapsed ? '»' : '« Collapse'}
        </button>
      </div>
    </aside>
  );
}
