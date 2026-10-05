import React from 'react';
import { Button, StatusDot } from './ui';
import { relativeTime } from '../lib/format';

/**
 * Application top bar: where the operator is, whether the last reconciliation
 * succeeded, how fresh the data is, and how to refresh it.
 */
export function TopBar({ title, lastRun, lastSync, hubConnected, onRefresh, refreshing }) {
  const runOk = lastRun?.status === 'success';
  const runLabel = lastRun
    ? `Last run ${lastRun.run_id} · ${lastRun.status}`
    : 'No reconciliation run yet';

  return (
    <header className="topbar">
      <div className="topbar__title">
        <h2>{title}</h2>
      </div>

      <div className="topbar__meta">
        <StatusDot
          ok={runOk}
          warn={lastRun && lastRun.status === 'running'}
          label={runLabel}
        />
        <span className="topbar__divider" aria-hidden="true" />
        <StatusDot ok={hubConnected} label={hubConnected ? 'Mojaloop connected' : 'Mojaloop unreachable'} />
        <span className="topbar__divider" aria-hidden="true" />
        <span className="topbar__sync">
          Last sync <strong>{lastSync ? relativeTime(lastSync) : '—'}</strong>
        </span>
      </div>

      <div className="topbar__actions">
        <Button variant="quiet" size="sm" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </Button>
        <div className="topbar__user" title="Signed in as treasury operations">
          <span className="topbar__avatar" aria-hidden="true">
            TO
          </span>
          <span className="topbar__user-text">
            <strong>Treasury Ops</strong>
            <em>Analyst</em>
          </span>
        </div>
      </div>
    </header>
  );
}
