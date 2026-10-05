import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { DataTable } from '../components/DataTable';
import {
  Badge,
  Button,
  Card,
  Field,
  FilterBar,
  Notice,
  PageHeader,
  Pagination,
  Select,
  TextInput
} from '../components/ui';
import { dateTime, humanise, number, riskScore } from '../lib/format';

const TYPES = [
  'duplicate_charge',
  'amount_mismatch',
  'missing_settlement',
  'timing_anomaly',
  'unrecognized_account',
  'currency_mismatch',
  'statistical_outlier'
];
const SEVERITIES = ['high', 'medium', 'low'];
const STATUSES = ['OPEN', 'RESOLVED', 'IGNORED'];
const PAGE_SIZE = 50;

/**
 * Exception queue.
 *
 * Resolving or ignoring an exception is recorded on the backend and respected
 * by later reconciliation runs, so triage is not undone by the next run.
 */
export function AnomaliesPage({ route, onNavigate, onRefresh }) {
  const [filters, setFilters] = useState({
    status: route.params.status || 'OPEN',
    anomaly_type: route.params.type || '',
    severity: route.params.severity || '',
    search: ''
  });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await api.anomalies({
        status: filters.status || undefined,
        anomaly_type: filters.anomaly_type || undefined,
        severity: filters.severity || undefined,
        limit: PAGE_SIZE,
        offset
      });
      setData(payload);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
      setData({ items: [], total: 0 });
    } finally {
      setLoading(false);
    }
  }, [filters, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const update = (key, value) => {
    setOffset(0);
    setFilters(current => ({ ...current, [key]: value }));
  };

  const act = async (row, status) => {
    setBusy(row.id);
    try {
      await api.resolveAnomaly(row.id, {
        status,
        note: status === 'RESOLVED' ? 'Reviewed by treasury operations' : 'Accepted as expected'
      });
      setNotice({ kind: 'success', text: `Exception ${row.id} marked ${status.toLowerCase()}.` });
      await Promise.all([load(), onRefresh()]);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(null);
    }
  };

  const visible = filters.search
    ? data.items.filter(row =>
        row.transaction_id.toLowerCase().includes(filters.search.toLowerCase())
      )
    : data.items;

  return (
    <>
      <PageHeader
        title="Exceptions"
        subtitle="Detected discrepancies requiring review, ranked by risk"
      />

      {notice && (
        <Notice kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      )}

      <Card dense>
        <FilterBar
          onReset={() => {
            setOffset(0);
            setFilters({ status: 'OPEN', anomaly_type: '', severity: '', search: '' });
          }}
        >
          <Field label="Status">
            <Select
              value={filters.status}
              onChange={v => update('status', v)}
              options={STATUSES}
              placeholder="All statuses"
            />
          </Field>
          <Field label="Type">
            <Select
              value={filters.anomaly_type}
              onChange={v => update('anomaly_type', v)}
              options={TYPES}
            />
          </Field>
          <Field label="Risk">
            <Select
              value={filters.severity}
              onChange={v => update('severity', v)}
              options={SEVERITIES}
            />
          </Field>
          <Field label="Transaction ID" width="220px">
            <TextInput
              value={filters.search}
              onChange={v => setFilters(c => ({ ...c, search: v }))}
              placeholder="Search this page"
            />
          </Field>
        </FilterBar>
      </Card>

      <Card title={`${number(data.total)} exception${data.total === 1 ? '' : 's'}`}>
        <DataTable
          loading={loading}
          rows={visible}
          rowKey={row => row.id}
          empty="No exceptions match these filters"
          maxHeight="62vh"
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
              render: row => dateTime(row.detected_at)
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
              key: 'run',
              header: 'Run',
              mono: true,
              render: row => row.run_id ?? <span className="muted">—</span>
            },
            {
              key: 'action',
              header: '',
              render: row =>
                row.status === 'OPEN' ? (
                  <div className="row-actions">
                    <Button
                      size="sm"
                      variant="quiet"
                      disabled={busy === row.id}
                      onClick={() => act(row, 'RESOLVED')}
                    >
                      Review
                    </Button>
                    <Button
                      size="sm"
                      variant="quiet"
                      disabled={busy === row.id}
                      onClick={() => act(row, 'IGNORED')}
                    >
                      Ignore
                    </Button>
                  </div>
                ) : (
                  <span className="muted">{row.resolution_note || 'Closed'}</span>
                )
            }
          ]}
          renderDetail={row => (
            <>
              <p className="explanation">{row.explanation}</p>
              {row.resolved_at && (
                <p className="muted small">
                  Closed {dateTime(row.resolved_at)}
                  {row.resolution_note ? ` · ${row.resolution_note}` : ''}
                </p>
              )}
            </>
          )}
        />
        <Pagination total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
      </Card>
    </>
  );
}
