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
  TextInput
} from '../components/ui';
import { dateTime, money, moneyShort, number } from '../lib/format';

/**
 * FX rates.
 *
 * Rates carry a validity window, which is what lets a historical
 * reconciliation be recomputed against the rate that applied at the time
 * rather than today's rate. Superseded rows are kept, not overwritten.
 */
export function FxRatesPage({ summary }) {
  const base = summary?.base_currency || 'USD';

  const [rates, setRates] = useState([]);
  const [includeHistory, setIncludeHistory] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);
  const [edits, setEdits] = useState({});

  const [convert, setConvert] = useState({ amount: '1000', from: 'INR', result: null });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await api.fxRates(includeHistory);
      setRates(payload.rates || []);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setLoading(false);
    }
  }, [includeHistory]);

  useEffect(() => {
    load();
  }, [load]);

  const refresh = async () => {
    setBusy('refresh');
    try {
      const res = await api.refreshFx();
      setNotice({
        kind: 'success',
        text: `Provider "${res.provider}" applied: ${number(res.updated)} rate(s) updated.`
      });
      await load();
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(null);
    }
  };

  const saveRate = async currency => {
    const value = Number(edits[currency]);
    if (!value || value <= 0) {
      setNotice({ kind: 'error', text: 'Units per base must be a positive number.' });
      return;
    }
    setBusy(currency);
    try {
      await api.setFxRate(currency, value);
      setNotice({
        kind: 'success',
        text: `${currency} set to ${value} per 1 ${base}. The previous rate is retained as history.`
      });
      setEdits(current => ({ ...current, [currency]: '' }));
      await load();
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(null);
    }
  };

  const runConvert = async () => {
    try {
      const res = await api.fxConvert({
        amount: Number(convert.amount),
        from_currency: convert.from.toUpperCase()
      });
      setConvert(c => ({ ...c, result: res }));
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    }
  };

  return (
    <>
      <PageHeader
        title="FX Rates"
        subtitle={`Conversion factors into the base currency (${base}), with validity windows`}
      >
        <div className="page-header__status">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={includeHistory}
              onChange={e => setIncludeHistory(e.target.checked)}
            />
            Show superseded rates
          </label>
          <Button variant="quiet" size="sm" onClick={refresh} disabled={busy === 'refresh'}>
            {busy === 'refresh' ? 'Refreshing…' : 'Refresh from provider'}
          </Button>
        </div>
      </PageHeader>

      {notice && (
        <Notice kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      )}

      <Card
        title={`${rates.length} rate${rates.length === 1 ? '' : 's'}`}
        subtitle="Editing a rate closes the current one and opens a new effective row, so earlier runs stay reproducible."
      >
        <DataTable
          loading={loading}
          rows={rates}
          rowKey={row => `${row.from_currency}-${row.valid_from || 'current'}`}
          empty="No FX rates stored"
          columns={[
            {
              key: 'pair',
              header: 'Pair',
              mono: true,
              render: row => (
                <strong>
                  {row.from_currency} → {row.to_currency}
                </strong>
              )
            },
            {
              key: 'units',
              header: `Units per 1 ${base}`,
              align: 'right',
              mono: true,
              render: row =>
                row.units_per_base == null ? '—' : Number(row.units_per_base).toFixed(4)
            },
            {
              key: 'rate',
              header: 'Multiplier to base',
              align: 'right',
              mono: true,
              render: row => Number(row.rate).toFixed(8)
            },
            {
              key: 'source',
              header: 'Source',
              render: row => <span className="muted">{row.source || '—'}</span>
            },
            {
              key: 'from',
              header: 'Valid from',
              mono: true,
              render: row => dateTime(row.valid_from)
            },
            {
              key: 'to',
              header: 'Valid to',
              mono: true,
              render: row =>
                row.valid_to ? dateTime(row.valid_to) : <span className="muted">current</span>
            },
            {
              key: 'current',
              header: '',
              render: row =>
                row.is_current ? (
                  <Badge value="current" tone="ok" label="Effective" />
                ) : (
                  <Badge value="superseded" tone="neutral" label="Superseded" />
                )
            },
            {
              key: 'edit',
              header: 'Override',
              render: row =>
                row.is_current && row.from_currency !== base ? (
                  <div className="inline-edit">
                    <input
                      className="input input--sm"
                      type="number"
                      step="0.0001"
                      min="0"
                      placeholder={Number(row.units_per_base).toFixed(4)}
                      value={edits[row.from_currency] || ''}
                      onChange={e =>
                        setEdits(c => ({ ...c, [row.from_currency]: e.target.value }))
                      }
                    />
                    <Button
                      size="sm"
                      variant="quiet"
                      disabled={busy === row.from_currency || !edits[row.from_currency]}
                      onClick={() => saveRate(row.from_currency)}
                    >
                      Save
                    </Button>
                  </div>
                ) : (
                  <span className="muted">—</span>
                )
            }
          ]}
        />
      </Card>

      <div className="grid grid--2">
        <Card title="Convert" subtitle="Uses the currently effective rate">
          <FilterBar>
            <Field label="Amount">
              <TextInput
                value={convert.amount}
                onChange={v => setConvert(c => ({ ...c, amount: v }))}
                type="number"
              />
            </Field>
            <Field label="From currency" width="140px">
              <TextInput
                value={convert.from}
                onChange={v => setConvert(c => ({ ...c, from: v.toUpperCase() }))}
                maxLength={3}
              />
            </Field>
            <Button variant="quiet" size="sm" onClick={runConvert}>
              Convert
            </Button>
          </FilterBar>

          {convert.result && (
            <table className="health">
              <tbody>
                <tr>
                  <th scope="row">Input</th>
                  <td>{money(convert.result.original_amount, convert.result.from_currency)}</td>
                </tr>
                <tr>
                  <th scope="row">Converted</th>
                  <td>
                    <strong>{money(convert.result.amount, convert.result.to_currency)}</strong>
                  </td>
                </tr>
                <tr>
                  <th scope="row">Rate applied</th>
                  <td className="is-mono">{Number(convert.result.rate).toFixed(8)}</td>
                </tr>
                <tr>
                  <th scope="row">Rate source</th>
                  <td>{convert.result.rate_source}</td>
                </tr>
                {convert.result.is_fallback && (
                  <tr>
                    <th scope="row">Warning</th>
                    <td className="warn-text">
                      No rate available for this currency; the amount was not converted.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Exposure by currency" subtitle={`Transaction value normalised to ${base}`}>
          <DataTable
            compact
            rows={summary?.currency_exposure || []}
            rowKey={row => row.currency}
            empty="No exposure recorded"
            columns={[
              {
                key: 'ccy',
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
                key: 'orig',
                header: 'Original',
                align: 'right',
                mono: true,
                render: row => moneyShort(row.value_original, row.currency)
              },
              {
                key: 'base',
                header: `In ${base}`,
                align: 'right',
                mono: true,
                render: row => moneyShort(row.value_base, base)
              }
            ]}
          />
        </Card>
      </div>
    </>
  );
}
