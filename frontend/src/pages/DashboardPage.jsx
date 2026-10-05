import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { DataTable } from '../components/DataTable';
import { CashFlowChart, StatusBreakdown } from '../components/charts';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Kpi,
  Notice,
  PageHeader,
  StatusDot
} from '../components/ui';
import {
  dateTime,
  duration,
  humanise,
  money,
  moneyCompact,
  moneyShort,
  number,
  percent,
  relativeTime,
  riskScore,
  timeOnly
} from '../lib/format';

/**
 * Reconciliation overview.
 *
 * Every figure is read from an existing backend endpoint. Where the backend
 * already aggregates something (status counts, FX exposure, ledger coverage,
 * confidence distribution) that aggregate is used directly rather than
 * recomputed here.
 */
export function DashboardPage({ summary, health, onNavigate, onRefresh }) {
  const [windows, setWindows] = useState([]);
  const [anomalies, setAnomalies] = useState([]);
  const [runs, setRuns] = useState([]);
  const [forecast, setForecast] = useState([]);
  const [history, setHistory] = useState([]);
  const [forecastMeta, setForecastMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);

  const base = summary?.base_currency || 'USD';

  const loadPanels = useCallback(async () => {
    setLoading(true);
    const [w, a, r, f, h, fm] = await Promise.all([
      api.windows({ limit: 6 }).catch(() => ({ items: [] })),
      api.anomalies({ status: 'OPEN', limit: 8 }).catch(() => ({ items: [] })),
      api.runs(6).catch(() => ({ runs: [] })),
      api.forecast(14).catch(() => []),
      api.forecastHistory(60).catch(() => ({ points: [] })),
      api.forecastMetadata().catch(() => null)
    ]);
    setWindows(w.items || []);
    setAnomalies(a.items || []);
    setRuns(r.runs || []);
    setForecast(Array.isArray(f) ? f : []);
    setHistory(h.points || []);
    setForecastMeta(fm);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadPanels();
  }, [loadPanels]);

  const runReconciliation = async () => {
    setBusy('run');
    setNotice(null);
    try {
      const result = await api.runReconciliation({ refresh_forecast: true });
      setNotice({
        kind: 'success',
        text:
          `Run ${result.run_id} finished in ${duration(result.duration_seconds)}: ` +
          `${number(result.matched_count)} matched, ${number(result.partial_count)} partial, ` +
          `${number(result.flagged_count)} held for review, ${number(result.anomalies_detected)} anomalies.`
      });
      await Promise.all([onRefresh(), loadPanels()]);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(null);
    }
  };

  if (!summary) return null;

  const statusCounts = {
    matched: summary.matched_transactions,
    partial: summary.partial_matched,
    flagged: summary.flagged_for_review,
    unmatched: summary.unmatched_transactions
  };
  const exceptions =
    (summary.partial_matched || 0) +
    (summary.flagged_for_review || 0) +
    (summary.unmatched_transactions || 0);
  const highRisk = summary.anomalies?.by_severity?.high || 0;

  return (
    <>
      <PageHeader
        title="Reconciliation Dashboard"
        subtitle="Payment and settlement reconciliation overview"
      >
        <div className="page-header__status">
          <StatusDot
            ok={health?.hub?.reachable}
            label={
              health?.enabled === false
                ? 'Mojaloop integration disabled'
                : health?.hub?.reachable
                  ? `Mojaloop connection healthy · ${health.hub.settlement_windows_visible ?? 0} windows visible`
                  : 'Mojaloop connection unavailable'
            }
          />
          <Button variant="primary" size="sm" onClick={runReconciliation} disabled={busy === 'run'}>
            {busy === 'run' ? 'Running…' : 'Run reconciliation'}
          </Button>
          <Button variant="quiet" size="sm" onClick={() => api.exportCsv()}>
            Export CSV
          </Button>
        </div>
      </PageHeader>

      {notice && (
        <Notice kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      )}

      {/* 2. KPI section ------------------------------------------------ */}
      <div className="kpi-row">
        <Kpi
          label="Total transactions"
          value={number(summary.total_transactions)}
          note={`${number(summary.transactions_last_24h)} in last 24h`}
          onClick={() => onNavigate('transactions')}
        />
        <Kpi
          label="Matched"
          value={number(summary.matched_transactions)}
          note={`${percent(summary.match_rate)} of book`}
          tone="ok"
          onClick={() => onNavigate('reconciliation', { status: 'matched' })}
        />
        <Kpi
          label="Exceptions"
          value={number(exceptions)}
          note="partial, flagged or unmatched"
          tone={exceptions ? 'warn' : undefined}
          onClick={() => onNavigate('reconciliation', { status: 'partial' })}
        />
        <Kpi
          label="Reconciliation rate"
          value={percent(summary.match_rate)}
          note={`${percent(summary.settled_rate)} incl. partial`}
        />
        <Kpi
          label="Settlement value"
          value={moneyCompact(summary.total_reconciled_value, base)}
          note={`base ${base}`}
        />
        <Kpi
          label="High risk"
          value={number(highRisk)}
          note={`${number(summary.anomaly_count)} open exceptions`}
          tone={highRisk ? 'bad' : undefined}
          onClick={() => onNavigate('anomalies', { severity: 'high' })}
        />
      </div>

      <div className="grid grid--2">
        {/* 3. Reconciliation overview ---------------------------------- */}
        <Card
          title="Reconciliation overview"
          subtitle={`Outcome mix across ${number(summary.total_transactions)} transactions`}
          actions={
            <Button variant="quiet" size="sm" onClick={() => onNavigate('reconciliation')}>
              View all
            </Button>
          }
        >
          <StatusBreakdown counts={statusCounts} total={summary.total_transactions} />

          <div className="subsection">
            <h3 className="subsection__title">Match confidence distribution</h3>
            <table className="mini-table">
              <tbody>
                {Object.entries(summary.confidence_distribution || {}).map(([bucket, count]) => (
                  <tr key={bucket}>
                    <td>{bucket === '0' ? 'No candidate' : `${bucket}%`}</td>
                    <td className="is-right is-mono">{number(count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {/* 7. FX exposure ---------------------------------------------- */}
        <Card
          title="FX exposure"
          subtitle={`Transaction value by currency, normalised to ${base}`}
          actions={
            <Button variant="quiet" size="sm" onClick={() => onNavigate('fx-rates')}>
              FX rates
            </Button>
          }
        >
          <DataTable
            compact
            rowKey={row => row.currency}
            rows={summary.currency_exposure || []}
            empty="No currency exposure recorded"
            columns={[
              {
                key: 'currency',
                header: 'Currency',
                render: row => (
                  <span className="ccy">
                    <strong>{row.currency}</strong>
                    {row.currency === base && <em className="ccy__base">base</em>}
                  </span>
                )
              },
              {
                key: 'txns',
                header: 'Txns',
                align: 'right',
                mono: true,
                render: row => number(row.transactions)
              },
              {
                key: 'original',
                header: 'Original value',
                align: 'right',
                mono: true,
                render: row => moneyShort(row.value_original, row.currency)
              },
              {
                key: 'base',
                header: `Value in ${base}`,
                align: 'right',
                mono: true,
                render: row => moneyShort(row.value_base, base)
              }
            ]}
          />

          <div className="subsection">
            <h3 className="subsection__title">Ledger coverage</h3>
            <div className="coverage">
              <CoverageRow
                label="Bank statement lines"
                done={summary.ledger_coverage?.bank_reconciled}
                total={summary.ledger_coverage?.bank_records}
                pct={summary.ledger_coverage?.bank_coverage_pct}
              />
              <CoverageRow
                label="ERP invoices"
                done={summary.ledger_coverage?.erp_cleared}
                total={summary.ledger_coverage?.erp_records}
                pct={summary.ledger_coverage?.erp_coverage_pct}
              />
            </div>
          </div>
        </Card>
      </div>

      {/* 4. Exceptions ------------------------------------------------- */}
      <Card
        title="Open exceptions"
        subtitle="Highest risk first. Review resolves the exception and records who closed it."
        actions={
          <Button variant="quiet" size="sm" onClick={() => onNavigate('anomalies')}>
            All exceptions
          </Button>
        }
      >
        <DataTable
          loading={loading}
          rows={anomalies}
          rowKey={row => row.id}
          empty="No open exceptions"
          emptyHint="Every flagged item has been reviewed."
          columns={[
            {
              key: 'txn',
              header: 'Transaction ID',
              mono: true,
              render: row => (
                <button
                  type="button"
                  className="link"
                  onClick={() => onNavigate('reconciliation', { search: row.transaction_id })}
                >
                  {row.transaction_id}
                </button>
              )
            },
            { key: 'type', header: 'Type', render: row => humanise(row.type) },
            {
              key: 'detector',
              header: 'Detected by',
              render: row => <span className="muted">{row.detector || '—'}</span>
            },
            {
              key: 'detected',
              header: 'Detected at',
              mono: true,
              render: row => timeOnly(row.detected_at)
            },
            {
              key: 'risk',
              header: 'Risk',
              align: 'right',
              mono: true,
              render: row => riskScore(row.risk_score)
            },
            { key: 'sev', header: 'Severity', render: row => <Badge value={row.severity} /> },
            { key: 'status', header: 'Status', render: row => <Badge value={row.status} /> },
            {
              key: 'action',
              header: '',
              render: row => (
                <Button
                  size="sm"
                  variant="quiet"
                  disabled={busy === row.id}
                  onClick={async () => {
                    setBusy(row.id);
                    try {
                      await api.resolveAnomaly(row.id, {
                        status: 'RESOLVED',
                        note: 'Reviewed from dashboard'
                      });
                      await Promise.all([onRefresh(), loadPanels()]);
                    } catch (err) {
                      setNotice({ kind: 'error', text: err.message });
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  Review
                </Button>
              )
            }
          ]}
          renderDetail={row => <p className="explanation">{row.explanation}</p>}
        />
      </Card>

      {/* 5. Settlement windows ----------------------------------------- */}
      <Card
        title="Recent settlement windows"
        subtitle="Reconciliation is scoped to a window, so a closed window is a repeatable unit of work."
        actions={
          <Button variant="quiet" size="sm" onClick={() => onNavigate('settlement-windows')}>
            Manage windows
          </Button>
        }
      >
        <DataTable
          loading={loading}
          rows={windows}
          rowKey={row => row.window_id}
          empty="No settlement windows stored"
          emptyHint="Sync windows from the Settlement Windows page."
          columns={[
            {
              key: 'window',
              header: 'Window',
              mono: true,
              render: row => (
                <button
                  type="button"
                  className="link"
                  onClick={() => onNavigate('reconciliation', { window: row.window_id })}
                >
                  {row.window_id}
                </button>
              )
            },
            { key: 'opened', header: 'Opened', mono: true, render: row => dateTime(row.scope_from) },
            { key: 'closed', header: 'Closed', mono: true, render: row => dateTime(row.scope_to) },
            {
              key: 'txns',
              header: 'Transactions',
              align: 'right',
              mono: true,
              render: row => number(row.transactions_in_window)
            },
            {
              key: 'matched',
              header: 'Matched',
              align: 'right',
              mono: true,
              render: row => (row.matched_count == null ? '—' : number(row.matched_count))
            },
            {
              key: 'exceptions',
              header: 'Exceptions',
              align: 'right',
              mono: true,
              render: row => (row.exception_count == null ? '—' : number(row.exception_count))
            },
            { key: 'state', header: 'Status', render: row => <Badge value={row.state} /> },
            {
              key: 'action',
              header: '',
              render: row =>
                row.is_reconcilable ? (
                  <Button
                    size="sm"
                    variant="quiet"
                    disabled={busy === row.window_id}
                    onClick={async () => {
                      setBusy(row.window_id);
                      try {
                        const res = await api.reconcileWindow(row.window_id);
                        setNotice({
                          kind: 'success',
                          text: `Window ${row.window_id}: ${number(res.matched_count)}/${number(
                            res.total_reconciled
                          )} matched (run ${res.run_id}).`
                        });
                        await Promise.all([onRefresh(), loadPanels()]);
                      } catch (err) {
                        setNotice({ kind: 'error', text: err.message });
                      } finally {
                        setBusy(null);
                      }
                    }}
                  >
                    {busy === row.window_id ? '…' : 'Reconcile'}
                  </Button>
                ) : (
                  <span className="muted">—</span>
                )
            }
          ]}
        />
      </Card>

      {/* 6. Cash flow --------------------------------------------------- */}
      <Card
        title="Cash flow"
        subtitle={
          forecastMeta?.model
            ? `Observed daily net flow with a ${forecastMeta.history_days}-day fitted forecast`
            : 'Observed daily net flow'
        }
        actions={
          <Button variant="quiet" size="sm" onClick={() => onNavigate('forecast')}>
            Forecast detail
          </Button>
        }
      >
        <CashFlowChart
          history={history}
          forecast={forecast}
          currency={base}
          model={forecastMeta?.model}
        />
      </Card>

      <div className="grid grid--2">
        {/* 8. Recent reconciliation runs ------------------------------- */}
        <Card
          title="Recent reconciliation runs"
          actions={
            <Button variant="quiet" size="sm" onClick={() => onNavigate('run-history')}>
              Full history
            </Button>
          }
        >
          <DataTable
            compact
            loading={loading}
            rows={runs}
            rowKey={row => row.run_id}
            empty="No runs recorded"
            columns={[
              { key: 'id', header: 'Run', mono: true, render: row => row.run_id },
              {
                key: 'window',
                header: 'Window',
                mono: true,
                render: row => row.settlement_window_id || <span className="muted">full book</span>
              },
              {
                key: 'started',
                header: 'Started',
                mono: true,
                render: row => dateTime(row.started_at)
              },
              {
                key: 'duration',
                header: 'Duration',
                align: 'right',
                mono: true,
                render: row => duration(row.duration_seconds)
              },
              {
                key: 'txns',
                header: 'Txns',
                align: 'right',
                mono: true,
                render: row => number(row.transactions_examined)
              },
              {
                key: 'matched',
                header: 'Matched',
                align: 'right',
                mono: true,
                render: row => number(row.matched_count)
              },
              { key: 'status', header: 'Status', render: row => <Badge value={row.status} /> }
            ]}
          />
        </Card>

        {/* 9. System health -------------------------------------------- */}
        <Card title="System health">
          <table className="health">
            <tbody>
              <tr>
                <th scope="row">Mojaloop</th>
                <td>
                  <StatusDot
                    ok={health?.hub?.reachable}
                    label={
                      health?.enabled === false
                        ? 'Disabled'
                        : health?.hub?.reachable
                          ? 'Connected'
                          : 'Unreachable'
                    }
                  />
                </td>
                <td className="muted is-right">
                  {health?.hub?.latency_ms ? `${Math.round(health.hub.latency_ms)} ms` : ''}
                </td>
              </tr>
              <tr>
                <th scope="row">Database</th>
                <td>
                  <StatusDot
                    ok={health?.ready?.status === 'ready'}
                    label={health?.ready?.status === 'ready' ? 'Healthy' : 'Unavailable'}
                  />
                </td>
                <td className="muted is-right">{health?.ready?.database || ''}</td>
              </tr>
              <tr>
                <th scope="row">Scheduler</th>
                <td>
                  <StatusDot
                    ok={health?.scheduler?.running && !health?.scheduler?.paused}
                    warn={health?.scheduler?.paused}
                    label={
                      !health?.scheduler?.running
                        ? 'Stopped'
                        : health?.scheduler?.paused
                          ? 'Paused'
                          : 'Running'
                    }
                  />
                </td>
                <td className="muted is-right">
                  {health?.scheduler?.interval_minutes
                    ? `every ${health.scheduler.interval_minutes} min`
                    : ''}
                </td>
              </tr>
              <tr>
                <th scope="row">Webhook</th>
                <td>
                  <StatusDot
                    ok
                    warn={!health?.signature_verification}
                    label={health?.signature_verification ? 'Active, signed' : 'Active, unsigned'}
                  />
                </td>
                <td className="muted is-right">
                  {health?.signature_verification ? 'HMAC verified' : 'no secret set'}
                </td>
              </tr>
              <tr>
                <th scope="row">Last sync</th>
                <td>{health?.lastSync ? relativeTime(health.lastSync) : '—'}</td>
                <td className="muted is-right">
                  {health?.lastSync ? dateTime(health.lastSync) : ''}
                </td>
              </tr>
            </tbody>
          </table>

          <div className="subsection">
            <h3 className="subsection__title">Data provenance</h3>
            <p className="muted small">{summary.synthetic_data_notice}</p>
            {summary.unnormalised_transactions > 0 && (
              <p className="warn-text small">
                {number(summary.unnormalised_transactions)} transaction(s) are not yet
                FX-normalised and are counted at face value.
              </p>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}

function CoverageRow({ label, done, total, pct }) {
  if (!total) return <EmptyState title={`No ${label.toLowerCase()} loaded`} />;
  return (
    <div className="coverage__row">
      <div className="coverage__head">
        <span>{label}</span>
        <span className="is-mono">
          {number(done)} / {number(total)} · {percent(pct)}
        </span>
      </div>
      <div className="coverage__track">
        <div className="coverage__fill" style={{ width: `${Math.min(100, pct || 0)}%` }} />
      </div>
    </div>
  );
}
