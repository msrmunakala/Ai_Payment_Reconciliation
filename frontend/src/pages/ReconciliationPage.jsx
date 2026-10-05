import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { DataTable, DetailGrid } from '../components/DataTable';
import {
  Badge,
  Button,
  Card,
  FilterBar,
  Field,
  Notice,
  PageHeader,
  Pagination,
  Select,
  TextInput
} from '../components/ui';
import { dateTime, humanise, money, number, percent } from '../lib/format';

const STATUSES = ['matched', 'partial', 'flag_for_review', 'unmatched'];
const TIERS = ['high', 'medium', 'low'];
const LEDGERS = ['BANK', 'ERP', 'MOJALOOP', 'NONE'];
const PAGE_SIZE = 50;

/**
 * Reconciliation results.
 *
 * All filtering and paging is done by the backend
 * (`GET /reconciliation/results`), so the table never holds more than one page
 * and the counts shown are authoritative rather than client-side estimates.
 */
export function ReconciliationPage({ summary, route, onNavigate, onRefresh }) {
  const base = summary?.base_currency || 'USD';

  const [filters, setFilters] = useState({
    status: route.params.status || '',
    tier: route.params.tier || '',
    ledger_type: route.params.ledger_type || '',
    settlement_window_id: route.params.window || '',
    manual_only: route.params.manual === 'true',
    search: route.params.search || ''
  });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setNotice(null);
    try {
      const payload = await api.results({
        status: filters.status || undefined,
        tier: filters.tier || undefined,
        ledger_type: filters.ledger_type || undefined,
        settlement_window_id: filters.settlement_window_id || undefined,
        manual_only: filters.manual_only || undefined,
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

  const reset = () => {
    setOffset(0);
    setFilters({
      status: '',
      tier: '',
      ledger_type: '',
      settlement_window_id: '',
      manual_only: false,
      search: ''
    });
  };

  // The results endpoint has no free-text search, so the ID filter is applied
  // to the current page client-side and labelled as such.
  const visible = filters.search
    ? data.items.filter(row =>
        `${row.transaction_id} ${row.ledger_id || ''}`
          .toLowerCase()
          .includes(filters.search.toLowerCase())
      )
    : data.items;

  const withdrawOverride = async transactionId => {
    setBusy(transactionId);
    try {
      await api.removeManualMatch(transactionId);
      setNotice({
        kind: 'success',
        text: `Override withdrawn for ${transactionId}. Re-run reconciliation to recompute it.`
      });
      await Promise.all([load(), onRefresh()]);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader
        title="Reconciliation Results"
        subtitle="Matching outcome per transaction, with the score that produced it"
      >
        <Button variant="quiet" size="sm" onClick={() => api.exportCsv(filters.status || undefined)}>
          Export CSV
        </Button>
      </PageHeader>

      {notice && (
        <Notice kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      )}

      <Card dense>
        <FilterBar onReset={reset}>
          <Field label="Status">
            <Select value={filters.status} onChange={v => update('status', v)} options={STATUSES} />
          </Field>
          <Field label="Confidence tier">
            <Select value={filters.tier} onChange={v => update('tier', v)} options={TIERS} />
          </Field>
          <Field label="Ledger">
            <Select
              value={filters.ledger_type}
              onChange={v => update('ledger_type', v)}
              options={LEDGERS}
            />
          </Field>
          <Field label="Settlement window">
            <TextInput
              value={filters.settlement_window_id}
              onChange={v => update('settlement_window_id', v)}
              placeholder="e.g. 9003"
            />
          </Field>
          <Field label="Transaction / ledger ID">
            <TextInput
              value={filters.search}
              onChange={v => setFilters(c => ({ ...c, search: v }))}
              placeholder="Search this page"
            />
          </Field>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={filters.manual_only}
              onChange={e => update('manual_only', e.target.checked)}
            />
            Manual matches only
          </label>
        </FilterBar>
      </Card>

      <Card
        title={`${number(data.total)} result${data.total === 1 ? '' : 's'}`}
        subtitle={
          filters.settlement_window_id
            ? `Scoped to settlement window ${filters.settlement_window_id}`
            : 'Full book'
        }
        actions={
          filters.settlement_window_id ? (
            <Button variant="quiet" size="sm" onClick={() => update('settlement_window_id', '')}>
              Clear window scope
            </Button>
          ) : null
        }
      >
        <DataTable
          loading={loading}
          rows={visible}
          rowKey={row => row.transaction_id}
          empty="No results match these filters"
          emptyHint="Widen the filters, or run reconciliation if the book has not been processed yet."
          maxHeight="60vh"
          columns={[
            {
              key: 'txn',
              header: 'Transaction ID',
              mono: true,
              render: row => (
                <button
                  type="button"
                  className="link"
                  onClick={() => onNavigate('transactions', { search: row.transaction_id })}
                >
                  {row.transaction_id}
                </button>
              )
            },
            { key: 'status', header: 'Status', render: row => <Badge value={row.status} /> },
            {
              key: 'confidence',
              header: 'Confidence',
              align: 'right',
              mono: true,
              render: row => percent(row.match_confidence)
            },
            { key: 'tier', header: 'Tier', render: row => <Badge value={row.confidence_tier} /> },
            {
              key: 'ledger',
              header: 'Matched to',
              mono: true,
              render: row =>
                row.ledger_id ? (
                  <>
                    {row.ledger_id}
                    <span className="muted"> · {row.ledger_type}</span>
                  </>
                ) : (
                  <span className="muted">—</span>
                )
            },
            {
              key: 'diff',
              header: `Difference (${base})`,
              align: 'right',
              mono: true,
              render: row =>
                row.amount_difference ? money(row.amount_difference, base) : <span className="muted">0.00</span>
            },
            {
              key: 'manual',
              header: 'Source',
              render: row =>
                row.is_manual ? <Badge value="manual" tone="info" label="Manual" /> : <span className="muted">Engine</span>
            },
            {
              key: 'at',
              header: 'Reconciled',
              mono: true,
              render: row => dateTime(row.reconciled_at)
            }
          ]}
          renderDetail={row => (
            <>
              <DetailGrid
                items={[
                  ['Amount score', percent(row.score_amount)],
                  ['Name score', percent(row.score_name)],
                  ['Reference score', percent(row.score_reference)],
                  ['Date score', percent(row.score_date)],
                  [
                    `Transaction (${row.base_currency || base})`,
                    money(row.transaction_amount_base, row.base_currency || base)
                  ],
                  [
                    `Ledger (${row.base_currency || base})`,
                    row.ledger_amount_base == null
                      ? '—'
                      : money(row.ledger_amount_base, row.base_currency || base)
                  ],
                  [
                    'Date difference',
                    row.date_difference_days == null
                      ? '—'
                      : `${row.date_difference_days.toFixed(2)} days`
                  ],
                  ['Ledger type', humanise(row.ledger_type)]
                ]}
              />
              <p className="explanation">{row.remarks}</p>
              {row.is_manual ? (
                <Button
                  size="sm"
                  variant="quiet"
                  disabled={busy === row.transaction_id}
                  onClick={() => withdrawOverride(row.transaction_id)}
                >
                  Withdraw manual override
                </Button>
              ) : null}
            </>
          )}
        />

        <Pagination
          total={data.total}
          limit={PAGE_SIZE}
          offset={offset}
          onChange={setOffset}
        />
      </Card>
    </>
  );
}
