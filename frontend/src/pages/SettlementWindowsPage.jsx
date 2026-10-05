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
  Pagination,
  Select,
  StatusDot
} from '../components/ui';
import { dateTime, duration, humanise, moneyShort, number } from '../lib/format';

const STATES = ['OPEN', 'CLOSED', 'PENDING_SETTLEMENT', 'SETTLED', 'ABORTED'];
const PAGE_SIZE = 25;

/**
 * Settlement windows.
 *
 * A window is the interval the hub considers final, which makes it the right
 * unit to reconcile: the boundary does not move, so the same run can be
 * repeated and compared. Reconciling a window only replaces results for the
 * transactions inside it.
 */
export function SettlementWindowsPage({ summary, health, onNavigate, onRefresh }) {
  const base = summary?.base_currency || 'USD';

  const [filters, setFilters] = useState({ state: '', reconciled: '' });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState({ items: [], total: 0 });
  const [settlements, setSettlements] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [w, s] = await Promise.all([
        api.windows({
          state: filters.state || undefined,
          reconciled: filters.reconciled === '' ? undefined : filters.reconciled === 'true',
          limit: PAGE_SIZE,
          offset
        }),
        api.settlements({ limit: 25 }).catch(() => ({ items: [] }))
      ]);
      setData(w);
      setSettlements(s.items || []);
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

  const hubReachable = health?.hub?.reachable;
  const pending = data.items.filter(w => w.is_reconcilable && !w.reconciled_at);

  const sync = async () => {
    setBusy('sync');
    setNotice(null);
    try {
      const res = await api.syncWindows();
      setNotice({
        kind: res.enabled === false || res.error ? 'warn' : 'success',
        text:
          res.enabled === false
            ? 'Mojaloop integration is disabled. Set MOJALOOP_ENABLED=true to pull windows.'
            : res.error
              ? `Hub unreachable: ${res.error}`
              : res.message
      });
      await Promise.all([load(), onRefresh()]);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(null);
    }
  };

  const reconcile = async windowId => {
    setBusy(windowId);
    try {
      const res = await api.reconcileWindow(windowId);
      setNotice({
        kind: 'success',
        text: `Window ${windowId}: ${number(res.matched_count)}/${number(
          res.total_reconciled
        )} matched, ${number(res.anomalies_detected)} exceptions (run ${res.run_id}).`
      });
      await Promise.all([load(), onRefresh()]);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(null);
    }
  };

  const reconcileAll = async () => {
    setBusy('all');
    try {
      const res = await api.reconcilePendingWindows();
      setNotice({ kind: 'success', text: res.message });
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
        title="Settlement Windows"
        subtitle="Hub settlement periods and the reconciliation run that covered each one"
      >
        <div className="page-header__status">
          <StatusDot
            ok={hubReachable}
            label={hubReachable ? 'Hub reachable' : 'Hub unreachable'}
          />
          <Button variant="quiet" size="sm" onClick={sync} disabled={busy === 'sync'}>
            {busy === 'sync' ? 'Syncing…' : 'Sync from hub'}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={reconcileAll}
            disabled={busy === 'all' || pending.length === 0}
          >
            {busy === 'all' ? 'Working…' : `Reconcile pending (${pending.length})`}
          </Button>
        </div>
      </PageHeader>

      {notice && (
        <Notice kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      )}

      <div className="kpi-row kpi-row--4">
        <Kpi label="Windows stored" value={number(data.total)} />
        <Kpi
          label="Awaiting reconciliation"
          value={number(pending.length)}
          tone={pending.length ? 'warn' : undefined}
          note="closed but not yet processed"
        />
        <Kpi
          label="Reconciled"
          value={number(data.items.filter(w => w.reconciled_at).length)}
          note="on this page"
        />
        <Kpi label="Settlements" value={number(settlements.length)} />
      </div>

      <Card dense>
        <FilterBar
          onReset={() => {
            setOffset(0);
            setFilters({ state: '', reconciled: '' });
          }}
        >
          <Field label="State">
            <Select
              value={filters.state}
              onChange={v => {
                setOffset(0);
                setFilters(c => ({ ...c, state: v }));
              }}
              options={STATES}
            />
          </Field>
          <Field label="Reconciliation">
            <Select
              value={filters.reconciled}
              onChange={v => {
                setOffset(0);
                setFilters(c => ({ ...c, reconciled: v }));
              }}
              options={[
                { value: 'true', label: 'Reconciled' },
                { value: 'false', label: 'Not reconciled' }
              ]}
            />
          </Field>
        </FilterBar>
      </Card>

      <Card title="Windows">
        <DataTable
          loading={loading}
          rows={data.items}
          rowKey={row => row.window_id}
          empty="No settlement windows stored"
          emptyHint="Press “Sync from hub” to pull windows from the Mojaloop settlement API."
          columns={[
            { key: 'id', header: 'Window', mono: true, render: row => <strong>{row.window_id}</strong> },
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
              render: row =>
                row.exception_count == null ? (
                  '—'
                ) : (
                  <span className={row.exception_count ? 'warn-text' : undefined}>
                    {number(row.exception_count)}
                  </span>
                )
            },
            { key: 'state', header: 'Status', render: row => <Badge value={row.state} /> },
            {
              key: 'action',
              header: '',
              render: row => (
                <div className="row-actions">
                  <Button
                    size="sm"
                    variant="quiet"
                    onClick={() => onNavigate('reconciliation', { window: row.window_id })}
                  >
                    Open
                  </Button>
                  {row.is_reconcilable && (
                    <Button
                      size="sm"
                      variant="quiet"
                      disabled={busy === row.window_id}
                      onClick={() => reconcile(row.window_id)}
                    >
                      {busy === row.window_id ? '…' : row.reconciled_at ? 'Re-run' : 'Reconcile'}
                    </Button>
                  )}
                </div>
              )
            }
          ]}
          renderDetail={row => (
            <DetailGrid
              items={[
                ['Hub state', humanise(row.state)],
                ['Reason', row.reason || '—'],
                ['Hub created', dateTime(row.created_date)],
                ['Hub changed', dateTime(row.changed_date)],
                ['Reconciled at', row.reconciled_at ? dateTime(row.reconciled_at) : 'Not reconciled'],
                ['Last run', row.last_run_id ?? '—'],
                ['Run status', row.run_status ? humanise(row.run_status) : '—'],
                ['Run duration', row.run_duration_seconds == null ? '—' : duration(row.run_duration_seconds)],
                ['Partial', row.partial_count == null ? '—' : number(row.partial_count)],
                ['Flagged', row.flagged_count == null ? '—' : number(row.flagged_count)],
                ['Unmatched', row.unmatched_count == null ? '—' : number(row.unmatched_count)],
                ['Last synced', dateTime(row.synced_at)]
              ]}
            />
          )}
        />
        <Pagination total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
      </Card>

      <Card
        title="Settlements"
        subtitle="Each settlement covers one or more windows. The hub has no list endpoint, so these are fetched by id."
      >
        <DataTable
          compact
          rows={settlements}
          rowKey={row => row.settlement_id}
          empty="No settlements stored"
          emptyHint="Fetch one with POST /settlement/settlements/sync?settlement_ids=…"
          columns={[
            { key: 'id', header: 'Settlement', mono: true, render: row => row.settlement_id },
            { key: 'state', header: 'State', render: row => <Badge value={row.state} /> },
            {
              key: 'model',
              header: 'Model',
              render: row => <span className="muted">{row.settlement_model || '—'}</span>
            },
            {
              key: 'windows',
              header: 'Windows',
              mono: true,
              render: row => (row.window_ids?.length ? row.window_ids.join(', ') : '—')
            },
            {
              key: 'participants',
              header: 'Participants',
              align: 'right',
              mono: true,
              render: row => number(row.participant_count)
            },
            {
              key: 'net',
              header: 'Net settled',
              align: 'right',
              mono: true,
              render: row =>
                row.net_amount == null ? (
                  <span className="muted" title="Mixed-currency settlement or no amounts reported">
                    —
                  </span>
                ) : (
                  moneyShort(row.net_amount, row.currency || base)
                )
            },
            { key: 'synced', header: 'Synced', mono: true, render: row => dateTime(row.synced_at) }
          ]}
        />
      </Card>
    </>
  );
}
