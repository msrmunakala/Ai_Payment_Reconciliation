import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { DataTable, DetailGrid } from '../components/DataTable';
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
import { dateTime, humanise, money, number } from '../lib/format';

const PAGE_SIZE = 50;

const LEDGERS = [
  { id: 'mojaloop', label: 'Mojaloop transfers' },
  { id: 'bank', label: 'Bank statement lines' },
  { id: 'erp', label: 'ERP invoices' }
];

/**
 * The three source ledgers behind reconciliation, each paginated and filtered
 * by the backend. Separate tabs rather than one merged table, because the
 * columns genuinely differ and merging them would hide the ledger a row is from.
 */
export function TransactionsPage({ summary, route, onNavigate }) {
  const base = summary?.base_currency || 'USD';
  const currencies = (summary?.currency_exposure || []).map(c => c.currency);

  const [ledger, setLedger] = useState('mojaloop');
  const [filters, setFilters] = useState({
    currency: '',
    status: '',
    source: '',
    search: route.params.search || ''
  });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setNotice(null);
    try {
      let payload;
      if (ledger === 'mojaloop') {
        payload = await api.transactions({
          currency: filters.currency || undefined,
          status: filters.status || undefined,
          source: filters.source || undefined,
          search: filters.search || undefined,
          limit: PAGE_SIZE,
          offset
        });
      } else if (ledger === 'bank') {
        payload = await api.bankRecords({
          status: filters.status || undefined,
          limit: PAGE_SIZE,
          offset
        });
      } else {
        payload = await api.erpRecords({
          status: filters.status || undefined,
          limit: PAGE_SIZE,
          offset
        });
      }
      setData(payload);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
      setData({ items: [], total: 0 });
    } finally {
      setLoading(false);
    }
  }, [ledger, filters, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const update = (key, value) => {
    setOffset(0);
    setFilters(current => ({ ...current, [key]: value }));
  };

  const switchLedger = id => {
    setLedger(id);
    setOffset(0);
    setFilters({ currency: '', status: '', source: '', search: '' });
  };

  const statusOptions =
    ledger === 'mojaloop'
      ? ['SUCCESS', 'PENDING', 'FAILED', 'REVERSED']
      : ledger === 'bank'
        ? ['UNRECONCILED', 'RECONCILED', 'PARTIALLY_RECONCILED']
        : ['OPEN', 'CLEARED', 'WRITTEN_OFF'];

  const columns =
    ledger === 'mojaloop'
      ? [
          {
            key: 'id',
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
          { key: 'ref', header: 'Reference', mono: true, render: row => row.reference_id || '—' },
          { key: 'payer', header: 'Payer', render: row => row.payer },
          { key: 'payee', header: 'Payee', render: row => row.payee },
          {
            key: 'amount',
            header: 'Amount',
            align: 'right',
            mono: true,
            render: row => money(row.amount, row.currency)
          },
          { key: 'ccy', header: 'Currency', render: row => row.currency },
          {
            key: 'base',
            header: `Value (${base})`,
            align: 'right',
            mono: true,
            render: row => (row.amount_base == null ? '—' : money(row.amount_base, base))
          },
          { key: 'state', header: 'Transfer state', render: row => <Badge value={row.transfer_state} /> },
          {
            key: 'source',
            header: 'Source',
            render: row => <span className="muted">{row.source || '—'}</span>
          },
          { key: 'created', header: 'Created', mono: true, render: row => dateTime(row.created_at) }
        ]
      : ledger === 'bank'
        ? [
            { key: 'id', header: 'Statement ID', mono: true, render: row => row.bank_statement_id },
            { key: 'acct', header: 'Account', mono: true, render: row => row.account_number },
            { key: 'cp', header: 'Counterparty', render: row => row.counterparty },
            {
              key: 'amount',
              header: 'Amount',
              align: 'right',
              mono: true,
              render: row => money(row.amount, row.currency)
            },
            { key: 'ccy', header: 'Currency', render: row => row.currency },
            { key: 'type', header: 'Type', render: row => <Badge value={row.transaction_type} /> },
            { key: 'ref', header: 'Reference', mono: true, render: row => row.reference_number || '—' },
            { key: 'date', header: 'Value date', mono: true, render: row => dateTime(row.value_date) },
            { key: 'status', header: 'Status', render: row => <Badge value={row.status} /> }
          ]
        : [
            { key: 'id', header: 'ERP ID', mono: true, render: row => row.erp_id },
            { key: 'inv', header: 'Invoice', mono: true, render: row => row.invoice_number || '—' },
            { key: 'party', header: 'Customer / vendor', render: row => row.customer_vendor_name },
            {
              key: 'amount',
              header: 'Expected amount',
              align: 'right',
              mono: true,
              render: row => money(row.expected_amount, row.currency)
            },
            { key: 'ccy', header: 'Currency', render: row => row.currency },
            { key: 'acct', header: 'Ledger account', mono: true, render: row => row.ledger_account },
            { key: 'date', header: 'Posting date', mono: true, render: row => dateTime(row.posting_date) },
            { key: 'status', header: 'Status', render: row => <Badge value={row.status} /> }
          ];

  return (
    <>
      <PageHeader
        title="Transactions &amp; Ledgers"
        subtitle="Mojaloop transfers, bank statement lines and ERP invoices"
      />

      {notice && (
        <Notice kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      )}

      <div className="tabs" role="tablist">
        {LEDGERS.map(item => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={ledger === item.id}
            className={`tab ${ledger === item.id ? 'is-active' : ''}`}
            onClick={() => switchLedger(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <Card dense>
        <FilterBar
          onReset={() => {
            setOffset(0);
            setFilters({ currency: '', status: '', source: '', search: '' });
          }}
        >
          <Field label="Status">
            <Select value={filters.status} onChange={v => update('status', v)} options={statusOptions} />
          </Field>
          {ledger === 'mojaloop' && (
            <>
              <Field label="Currency">
                <Select
                  value={filters.currency}
                  onChange={v => update('currency', v)}
                  options={currencies}
                />
              </Field>
              <Field label="Ingested via">
                <Select
                  value={filters.source}
                  onChange={v => update('source', v)}
                  options={['mojaloop', 'mojaloop-sim', 'upload', 'api', 'seed']}
                />
              </Field>
              <Field label="Search" width="240px">
                <TextInput
                  value={filters.search}
                  onChange={v => update('search', v)}
                  placeholder="Payer, payee, reference or ID"
                />
              </Field>
            </>
          )}
        </FilterBar>
      </Card>

      <Card title={`${number(data.total)} record${data.total === 1 ? '' : 's'}`}>
        <DataTable
          loading={loading}
          rows={data.items}
          rowKey={row => row.transaction_id || row.bank_statement_id || row.erp_id}
          empty="No records match these filters"
          maxHeight="60vh"
          columns={columns}
          renderDetail={
            ledger === 'mojaloop'
              ? row => (
                  <DetailGrid
                    items={[
                      ['Payer FSP', row.payer_fsp],
                      ['Payee FSP', row.payee_fsp],
                      ['Status', humanise(row.status)],
                      ['FX rate used', row.fx_rate_used ?? '—'],
                      ['Base currency', row.base_currency || '—'],
                      ['Batch', row.source_batch_id || '—']
                    ]}
                  />
                )
              : undefined
          }
        />
        <Pagination total={data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
      </Card>
    </>
  );
}
