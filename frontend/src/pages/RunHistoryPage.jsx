import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { DataTable, DetailGrid } from '../components/DataTable';
import {
  Badge,
  Button,
  Card,
  Field,
  FilterBar,
  Kpi,
  Notice,
  PageHeader,
  Select
} from '../components/ui';
import { dateTime, duration, humanise, number, percent } from '../lib/format';

/**
 * Reconciliation run history.
 *
 * Every run is recorded with its scope and parameters, which is what makes a
 * result auditable: you can see what was reconciled, over what boundary, with
 * what thresholds, and how long it took.
 */
export function RunHistoryPage({ onNavigate, onRefresh }) {
  const [limit, setLimit] = useState('25');
  const [scope, setScope] = useState('');
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await api.runs(Number(limit));
      setRuns(payload.runs || []);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    load();
  }, [load]);

  const runNow = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await api.runReconciliation({ refresh_forecast: true });
      setNotice({
        kind: 'success',
        text: `Run ${res.run_id} finished in ${duration(res.duration_seconds)} — ${number(
          res.matched_count
        )}/${number(res.total_reconciled)} matched.`
      });
      await Promise.all([load(), onRefresh()]);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(false);
    }
  };

  const visible = scope ? runs.filter(r => (r.settlement_window_id ? scope === 'window' : scope === 'full')) : runs;
  const successful = runs.filter(r => r.status === 'success').length;
  const avgDuration =
    runs.length > 0 ? runs.reduce((a, r) => a + (r.duration_seconds || 0), 0) / runs.length : 0;

  return (
    <>
      <PageHeader
        title="Run History"
        subtitle="Audit trail of every reconciliation execution"
      >
        <Button variant="primary" size="sm" onClick={runNow} disabled={busy}>
          {busy ? 'Running…' : 'Run reconciliation now'}
        </Button>
      </PageHeader>

      {notice && (
        <Notice kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      )}

      <div className="kpi-row kpi-row--4">
        <Kpi label="Runs recorded" value={number(runs.length)} note={`last ${limit}`} />
        <Kpi
          label="Successful"
          value={number(successful)}
          note={runs.length ? percent((successful / runs.length) * 100) : '—'}
          tone={successful === runs.length ? 'ok' : 'warn'}
        />
        <Kpi label="Mean duration" value={duration(avgDuration)} />
        <Kpi
          label="Window-scoped"
          value={number(runs.filter(r => r.settlement_window_id).length)}
          note="vs full-book runs"
        />
      </div>

      <Card dense>
        <FilterBar onReset={() => { setScope(''); setLimit('25'); }}>
          <Field label="Scope">
            <Select
              value={scope}
              onChange={setScope}
              options={[
                { value: 'window', label: 'Settlement window' },
                { value: 'full', label: 'Full book' }
              ]}
            />
          </Field>
          <Field label="Show">
            <Select
              value={limit}
              onChange={setLimit}
              placeholder=""
              options={[
                { value: '10', label: 'Last 10' },
                { value: '25', label: 'Last 25' },
                { value: '50', label: 'Last 50' },
                { value: '100', label: 'Last 100' }
              ]}
            />
          </Field>
        </FilterBar>
      </Card>

      <Card title={`${visible.length} run${visible.length === 1 ? '' : 's'}`}>
        <DataTable
          loading={loading}
          rows={visible}
          rowKey={row => row.run_id}
          empty="No reconciliation runs recorded"
          maxHeight="60vh"
          columns={[
            { key: 'id', header: 'Run ID', mono: true, render: row => <strong>{row.run_id}</strong> },
            {
              key: 'window',
              header: 'Settlement window',
              mono: true,
              render: row =>
                row.settlement_window_id ? (
                  <button
                    type="button"
                    className="link"
                    onClick={() =>
                      onNavigate('reconciliation', { window: row.settlement_window_id })
                    }
                  >
                    {row.settlement_window_id}
                  </button>
                ) : (
                  <span className="muted">full book</span>
                )
            },
            { key: 'trigger', header: 'Trigger', render: row => humanise(row.trigger) },
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
              header: 'Transactions',
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
            {
              key: 'exceptions',
              header: 'Exceptions',
              align: 'right',
              mono: true,
              render: row => {
                const total =
                  (row.partial_count || 0) + (row.flagged_count || 0) + (row.unmatched_count || 0);
                return <span className={total ? 'warn-text' : undefined}>{number(total)}</span>;
              }
            },
            { key: 'status', header: 'Status', render: row => <Badge value={row.status} /> }
          ]}
          renderDetail={row => (
            <>
              <DetailGrid
                items={[
                  ['Scope', humanise(row.scope || 'full')],
                  ['Finished', dateTime(row.finished_at)],
                  ['Candidates evaluated', number(row.candidates_evaluated)],
                  ['Partial', number(row.partial_count)],
                  ['Flagged for review', number(row.flagged_count)],
                  ['Unmatched', number(row.unmatched_count)],
                  ['Anomalies raised', number(row.anomaly_count)],
                  ['Manual overrides applied', number(row.manual_overrides_applied)],
                  ['Base currency', row.base_currency || '—']
                ]}
              />
              {row.error_message && <p className="error-text">{row.error_message}</p>}
            </>
          )}
        />
      </Card>
    </>
  );
}
