import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { DataTable } from '../components/DataTable';
import { CashFlowChart } from '../components/charts';
import {
  Button,
  Card,
  Field,
  FilterBar,
  Kpi,
  Notice,
  PageHeader,
  Select
} from '../components/ui';
import { dateOnly, humanise, money, moneyCompact, number, relativeTime } from '../lib/format';

/**
 * Cash-flow forecast.
 *
 * Presented as a model output with its provenance attached, not as a
 * prediction to be taken on faith: the fitted model, the amount of history it
 * saw, and whether any synthetic padding was involved are all stated.
 */
export function ForecastPage({ summary }) {
  const base = summary?.base_currency || 'USD';

  const [horizon, setHorizon] = useState('14');
  const [lookback, setLookback] = useState('90');
  const [forecast, setForecast] = useState([]);
  const [history, setHistory] = useState([]);
  const [meta, setMeta] = useState(null);
  const [notes, setNotes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [f, h, m] = await Promise.all([
        api.forecast(Number(horizon)),
        api.forecastHistory(Number(lookback)),
        api.forecastMetadata()
      ]);
      setForecast(Array.isArray(f) ? f : []);
      setHistory(h.points || []);
      setNotes(h.notes || []);
      setMeta(m);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setLoading(false);
    }
  }, [horizon, lookback]);

  useEffect(() => {
    load();
  }, [load]);

  const retrain = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await api.runForecast(Number(horizon));
      setNotice({
        kind: 'success',
        text: `Refitted using ${res.metadata?.model} over ${res.metadata?.history_days} days of history.`
      });
      await load();
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(false);
    }
  };

  const totalIn = forecast.reduce((acc, p) => acc + (p.inflow || 0), 0);
  const totalOut = forecast.reduce((acc, p) => acc + (p.outflow || 0), 0);
  const net = forecast.reduce((acc, p) => acc + (p.predicted_amount || 0), 0);
  const avgBand =
    forecast.length > 0
      ? forecast.reduce((acc, p) => acc + (p.upper_bound - p.lower_bound) / 2, 0) / forecast.length
      : 0;

  return (
    <>
      <PageHeader
        title="Cash-Flow Forecast"
        subtitle="Projected liquidity position, fitted to observed settlement history"
      >
        <div className="page-header__status">
          <span className="model-tag">
            Forecast model: <strong>{meta?.model ? humanise(meta.model) : '—'}</strong>
            {meta?.model_version ? <em> v{meta.model_version}</em> : null}
          </span>
          <Button variant="primary" size="sm" onClick={retrain} disabled={busy}>
            {busy ? 'Refitting…' : 'Refit model'}
          </Button>
        </div>
      </PageHeader>

      {notice && (
        <Notice kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      )}

      {meta?.is_synthetic && (
        <Notice kind="warn">
          This forecast includes synthetic padding because there was not enough observed
          history to fit a model. Treat it as a placeholder.
        </Notice>
      )}

      <div className="kpi-row kpi-row--4">
        <Kpi
          label={`Projected inflow (${horizon}d)`}
          value={moneyCompact(totalIn, base)}
          tone="ok"
        />
        <Kpi
          label={`Projected outflow (${horizon}d)`}
          value={moneyCompact(totalOut, base)}
          tone={totalOut ? 'bad' : undefined}
        />
        <Kpi label={`Net position (${horizon}d)`} value={moneyCompact(net, base)} />
        <Kpi
          label="Mean confidence band"
          value={`± ${moneyCompact(avgBand, base)}`}
          note="95% interval"
        />
      </div>

      <Card dense>
        <FilterBar>
          <Field label="Forecast horizon">
            <Select
              value={horizon}
              onChange={setHorizon}
              placeholder=""
              options={[
                { value: '7', label: '7 days' },
                { value: '14', label: '14 days' },
                { value: '30', label: '30 days' }
              ]}
            />
          </Field>
          <Field label="History shown">
            <Select
              value={lookback}
              onChange={setLookback}
              placeholder=""
              options={[
                { value: '30', label: '30 days' },
                { value: '60', label: '60 days' },
                { value: '90', label: '90 days' },
                { value: '180', label: '180 days' }
              ]}
            />
          </Field>
          <div className="filter-bar__note">
            {meta?.history_days != null && (
              <>
                Fitted on <strong>{number(meta.history_days)}</strong> days ·{' '}
                {meta.generated_at ? `generated ${relativeTime(meta.generated_at)}` : ''}
              </>
            )}
          </div>
        </FilterBar>
      </Card>

      <Card title="Net cash flow" subtitle="Observed history and forecast on one axis">
        {loading ? (
          <p className="muted">Loading series…</p>
        ) : (
          <CashFlowChart
            history={history}
            forecast={forecast}
            currency={base}
            model={meta?.model}
          />
        )}
      </Card>

      <div className="grid grid--2">
        <Card title="Forecast detail">
          <DataTable
            compact
            rows={forecast}
            rowKey={row => row.date}
            empty="No forecast generated"
            columns={[
              { key: 'date', header: 'Date', mono: true, render: row => dateOnly(row.date) },
              {
                key: 'net',
                header: 'Net',
                align: 'right',
                mono: true,
                render: row => money(row.predicted_amount, base)
              },
              {
                key: 'in',
                header: 'Inflow',
                align: 'right',
                mono: true,
                render: row => money(row.inflow, base)
              },
              {
                key: 'out',
                header: 'Outflow',
                align: 'right',
                mono: true,
                render: row => money(row.outflow, base)
              },
              {
                key: 'range',
                header: '95% interval',
                align: 'right',
                mono: true,
                render: row => `${money(row.lower_bound, base)} – ${money(row.upper_bound, base)}`
              }
            ]}
          />
        </Card>

        <Card title="Model provenance">
          <table className="health">
            <tbody>
              <tr>
                <th scope="row">Model</th>
                <td>{meta?.model ? humanise(meta.model) : '—'}</td>
              </tr>
              <tr>
                <th scope="row">Version</th>
                <td>{meta?.model_version || '—'}</td>
              </tr>
              <tr>
                <th scope="row">History fitted</th>
                <td>{meta?.history_days != null ? `${number(meta.history_days)} days` : '—'}</td>
              </tr>
              <tr>
                <th scope="row">Horizon stored</th>
                <td>{meta?.horizon_days != null ? `${meta.horizon_days} days` : '—'}</td>
              </tr>
              <tr>
                <th scope="row">Currency</th>
                <td>{meta?.currency || base}</td>
              </tr>
              <tr>
                <th scope="row">Prophet available</th>
                <td>{meta?.prophet_installed ? 'Yes' : 'No — using fallback model'}</td>
              </tr>
              <tr>
                <th scope="row">Generated</th>
                <td>{meta?.generated_at ? relativeTime(meta.generated_at) : '—'}</td>
              </tr>
              <tr>
                <th scope="row">Cache TTL</th>
                <td>
                  {meta?.cache_ttl_seconds != null ? `${meta.cache_ttl_seconds}s` : '—'}
                </td>
              </tr>
            </tbody>
          </table>

          {notes.length > 0 && (
            <div className="subsection">
              <h3 className="subsection__title">How the series was derived</h3>
              <ul className="notes">
                {notes.map(note => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
