import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const API_KEY = import.meta.env.VITE_API_KEY || 'review2-demo-key';
const headers = { 'X-API-Key': API_KEY };

// Figures from the backend are normalised to its base currency. Formatting them
// as INR (as this file previously did) mislabels USD values with a rupee symbol.
const makeMoneyFormatter = (currency = 'USD') => {
  const formatter = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 0
  });
  return value => formatter.format(value || 0);
};

function App() {
  const [summary, setSummary] = useState(null);
  const [results, setResults] = useState([]);
  const [anomalies, setAnomalies] = useState([]);
  const [forecast, setForecast] = useState([]);
  const [filter, setFilter] = useState('');
  const [resultTotal, setResultTotal] = useState(0);
  const [showScriptModal, setShowScriptModal] = useState(false);
  const [generatorCode, setGeneratorCode] = useState(null);
  
  // Forecast interactive state
  const [forecastDays, setForecastDays] = useState(7);
  const [forecastView, setForecastView] = useState('trend'); // 'trend', 'bars', 'table'
  const [activePoint, setActivePoint] = useState(null);
  const [forecastModel, setForecastModel] = useState(null);
  const [forecastCurrency, setForecastCurrency] = useState(null);
  const [forecastSynthetic, setForecastSynthetic] = useState(false);

  // Settlement windows. A window is the period the hub considers closed, and is
  // the unit a reconciliation run is scoped to.
  const [windows, setWindows] = useState([]);
  const [settlements, setSettlements] = useState([]);
  const [hubStatus, setHubStatus] = useState(null);
  const [windowFilter, setWindowFilter] = useState('');   // scopes the results table
  const [busyWindow, setBusyWindow] = useState(null);     // window id being reconciled
  const [windowNotice, setWindowNotice] = useState(null);

  // Currency used for every displayed figure, taken from the backend.
  const baseCurrency = summary?.base_currency || forecastCurrency || 'USD';
  const money = React.useMemo(() => makeMoneyFormatter(baseCurrency), [baseCurrency]);

  const fetchForecast = async (days = forecastDays) => {
    try {
      const res = await fetch(`${API}/forecast?days=${days}`, { headers });
      const f = await res.json();
      const points = Array.isArray(f) ? f : [];
      setForecast(points);
      if (points.length > 0) {
        setActivePoint(points[0]);
        // Model provenance travels with each point.
        setForecastModel(points[0].model || null);
        setForecastCurrency(points[0].currency || null);
        setForecastSynthetic(Boolean(points[0].is_synthetic));
      }
    } catch (err) {
      console.error('Error loading forecast:', err);
    }
  };

  const fetchScript = async () => {
    try {
      const res = await fetch(`${API}/demo/generator-script`, { headers });
      const data = await res.json();
      setGeneratorCode(data);
      setShowScriptModal(true);
    } catch (err) {
      console.error('Error fetching generator script:', err);
    }
  };

  const downloadJSON = () => {
    fetch(`${API}/demo/export-json`, { headers })
      .then(r => r.blob())
      .then(b => {
        const u = URL.createObjectURL(b), a = document.createElement('a');
        a.href = u;
        a.download = 'generated-dataset.json';
        a.click();
        URL.revokeObjectURL(u);
      });
  };

  const downloadCSV = () => {
    fetch(`${API}/reports/export.csv`, { headers })
      .then(r => r.blob())
      .then(b => {
        const u = URL.createObjectURL(b), a = document.createElement('a');
        a.href = u;
        a.download = 'reconciliation-report.csv';
        a.click();
        URL.revokeObjectURL(u);
      });
  };

  // The results and anomalies endpoints are paginated. Accept either a bare
  // array (older builds) or a { items, total } envelope.
  const unwrap = payload => (Array.isArray(payload) ? payload : payload?.items ?? []);

  const jsonOrNull = url =>
    fetch(url, { headers })
      .then(r => (r.ok ? r.json() : null))
      .catch(() => null);

  // Results are optionally scoped to one settlement window, which the backend
  // resolves via the window's time interval.
  const loadResults = async (windowId = windowFilter) => {
    const scope = windowId ? `&settlement_window_id=${encodeURIComponent(windowId)}` : '';
    const r = await jsonOrNull(`${API}/reconciliation/results?limit=1000${scope}`);
    setResults(unwrap(r));
    setResultTotal(Array.isArray(r) ? r.length : r?.total ?? 0);
  };

  const loadSettlement = async () => {
    const [w, s, st] = await Promise.all([
      jsonOrNull(`${API}/settlement/windows?limit=50`),
      jsonOrNull(`${API}/settlement/settlements?limit=20`),
      jsonOrNull(`${API}/webhooks/status`)
    ]);
    setWindows(unwrap(w));
    setSettlements(unwrap(s));
    setHubStatus(st?.integration ?? null);
  };

  const load = async () => {
    const [s, a] = await Promise.all([
      jsonOrNull(`${API}/dashboard/summary`),
      jsonOrNull(`${API}/anomalies?limit=500`)
    ]);
    setSummary(s);
    setAnomalies(unwrap(a));
    await Promise.all([loadResults(), loadSettlement(), fetchForecast(forecastDays)]);
  };

  // Pull the latest windows from the hub, then refresh the table.
  const syncWindows = async () => {
    setWindowNotice('Pulling settlement windows from the hub...');
    try {
      const res = await fetch(`${API}/settlement/sync`, { method: 'POST', headers });
      const body = await res.json();
      setWindowNotice(
        body?.enabled === false
          ? 'Mojaloop integration is disabled (MOJALOOP_ENABLED=false).'
          : body?.error
            ? `Hub unreachable: ${body.error}`
            : `Synced ${body.fetched ?? 0} window(s): ${body.created ?? 0} new, ${body.updated ?? 0} updated.`
      );
      await loadSettlement();
    } catch (err) {
      setWindowNotice(`Sync failed: ${err.message}`);
    }
  };

  // Reconcile one window. Only results for transactions inside it are replaced.
  const reconcileWindow = async windowId => {
    setBusyWindow(windowId);
    setWindowNotice(`Reconciling window ${windowId}...`);
    try {
      const res = await fetch(
        `${API}/settlement/windows/${encodeURIComponent(windowId)}/reconcile`,
        { method: 'POST', headers }
      );
      const body = await res.json();
      if (!res.ok) {
        setWindowNotice(`Window ${windowId}: ${body?.detail ?? 'reconciliation failed'}`);
      } else {
        setWindowNotice(
          `Window ${windowId}: ${body.matched_count}/${body.total_reconciled} matched, ` +
          `${body.partial_count} partial, ${body.flagged_count} held for review, ` +
          `${body.anomalies_detected} anomalies (run ${body.run_id}).`
        );
      }
      await load();
    } catch (err) {
      setWindowNotice(`Window ${windowId} failed: ${err.message}`);
    } finally {
      setBusyWindow(null);
    }
  };

  const reconcilePending = async () => {
    setBusyWindow('pending');
    setWindowNotice('Reconciling every closed window that is still outstanding...');
    try {
      const res = await fetch(`${API}/settlement/windows/reconcile-pending`, {
        method: 'POST',
        headers
      });
      const body = await res.json();
      setWindowNotice(body?.message ?? 'Done.');
      await load();
    } catch (err) {
      setWindowNotice(`Failed: ${err.message}`);
    } finally {
      setBusyWindow(null);
    }
  };

  const applyWindowFilter = async windowId => {
    setWindowFilter(windowId);
    await loadResults(windowId);
  };

  useEffect(() => {
    load().catch(console.error);
  }, []);

  const handleHorizonChange = (days) => {
    setForecastDays(days);
    fetchForecast(days);
  };

  const run = async () => {
    await fetch(`${API}/reconciliation/run`, { method: 'POST', headers });
    await load();
  };

  const resetDemo = async (count = 150) => {
    await fetch(`${API}/demo/reset?count=${count}`, { method: 'POST', headers });
    await load();
  };

  const visible = results.filter(item => !filter || item.status === filter);

  // Forecast aggregations
  const totalInflow = forecast.reduce((acc, p) => acc + (p.inflow || p.predicted_amount * 1.25), 0);
  const totalOutflow = forecast.reduce((acc, p) => acc + (p.outflow || p.predicted_amount * 0.25), 0);
  const netLiquidity = forecast.reduce((acc, p) => acc + p.predicted_amount, 0);
  const avgConfidenceMargin = forecast.length > 0 
    ? forecast.reduce((acc, p) => acc + (p.upper_bound - p.lower_bound) / 2, 0) / forecast.length 
    : 0;

  // SVG Chart layout calculation
  const svgWidth = 640;
  const svgHeight = 220;
  const paddingX = 40;
  const paddingY = 30;

  let maxY = 1;
  let minY = 0;
  if (forecast.length > 0) {
    maxY = Math.max(...forecast.map(p => Math.max(p.upper_bound || 0, p.inflow || 0, p.predicted_amount || 0))) * 1.1;
    minY = Math.min(0, ...forecast.map(p => p.lower_bound || 0));
  }

  const getX = (index) => {
    if (forecast.length <= 1) return svgWidth / 2;
    return paddingX + (index / (forecast.length - 1)) * (svgWidth - 2 * paddingX);
  };

  const getY = (val) => {
    const usableH = svgHeight - 2 * paddingY;
    return svgHeight - paddingY - ((val - minY) / (maxY - minY)) * usableH;
  };

  // Construct SVG paths for Confidence Area Band & Net Trend Line
  let confidencePath = '';
  let trendPath = '';
  if (forecast.length > 0) {
    // Upper bound forward path
    const upperPoints = forecast.map((p, i) => `${getX(i)},${getY(p.upper_bound)}`);
    // Lower bound reverse path
    const lowerPoints = [...forecast].reverse().map((p, i) => {
      const origIdx = forecast.length - 1 - i;
      return `${getX(origIdx)},${getY(p.lower_bound)}`;
    });
    confidencePath = `M ${upperPoints.join(' L ')} L ${lowerPoints.join(' L ')} Z`;

    const trendPoints = forecast.map((p, i) => `${getX(i)},${getY(p.predicted_amount)}`);
    trendPath = `M ${trendPoints.join(' L ')}`;
  }

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">AI-on-DPI • Review 2 prototype</p>
          <h1>Payment Reconciliation</h1>
          <p>Explainable Mojaloop-to-ERP reconciliation for treasury review & ML liquidity forecasting.</p>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button onClick={() => resetDemo(150)} style={{ background: '#3b82f6' }}>⚡ Generate 150 Data</button>
          <button onClick={downloadJSON} style={{ background: '#10b981' }}>📥 Download Data (JSON)</button>
          <button onClick={downloadCSV} style={{ background: '#059669' }}>📄 Download CSV</button>
          <button onClick={fetchScript} style={{ background: '#8b5cf6' }}>📜 View Generator Script</button>
          <button onClick={run} style={{ background: '#63d7cf', color: '#0d131f' }}>Run reconciliation</button>
        </div>
      </header>

      <aside>Demo notice: ERP-side records are synthetic. This dashboard is not connected to a production bank or ERP.</aside>

      {/* KPI Cards */}
      <section className="kpis">
        {summary && [
          ["Transactions", summary.total_transactions],
          ["Match rate", `${summary.match_rate}%`],
          ["Flagged anomalies", summary.anomaly_count],
          ["Reconciled value", money(summary.total_reconciled_value)]
        ].map(([label, value]) => (
          <article key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </section>

      <section className="grid">
        {/* Main Reconciliation Table */}
        <article className="panel wide">
          <div className="panel-head">
            <h2>
              Reconciliation results ({visible.length}
              {resultTotal > results.length ? ` of ${resultTotal}` : ''} records)
              {windowFilter && (
                <span className="scope-chip">
                  settlement window {windowFilter}
                  <button
                    className="scope-clear"
                    title="Show all windows"
                    onClick={() => applyWindowFilter('')}
                  >
                    ✕
                  </button>
                </span>
              )}
            </h2>
            <select value={filter} onChange={e => setFilter(e.target.value)}>
              <option value="">All statuses</option>
              {['matched', 'partial', 'flag_for_review', 'unmatched'].map(x => <option key={x}>{x}</option>)}
            </select>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Transaction</th>
                  <th>Status</th>
                  <th>Confidence</th>
                  <th>Difference</th>
                  <th>Explanation</th>
                </tr>
              </thead>
              <tbody>
                {visible.map(row => (
                  <tr key={row.transaction_id}>
                    <td>{row.transaction_id}</td>
                    <td><b className={`tag ${row.status}`}>{row.status.replaceAll('_', ' ')}</b></td>
                    <td>{row.match_confidence}%</td>
                    <td>{money(row.amount_difference)}</td>
                    <td>{row.remarks}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        {/* Enhanced Cash-Flow Forecast Graph Section */}
        <article className="panel full-width">
          <div className="panel-head">
            <div>
              <h2>Cash-Flow Liquidity Forecast</h2>
              <p className="muted" style={{ fontSize: '0.8rem', margin: '2px 0 0 0' }}>
                {forecastModel
                  ? `${forecastModel === 'prophet' ? 'Prophet' : forecastModel === 'ridge' ? 'Ridge regression' : 'Seasonal naive'} model, 95% confidence interval, values in ${forecastCurrency || 'USD'}.`
                  : 'Time-series model with 95% confidence intervals.'}
                {forecastSynthetic ? ' Synthetic placeholder: insufficient history.' : ''}
              </p>
            </div>
            
            <div className="forecast-controls">
              {/* Horizon Selector */}
              <div className="btn-group">
                <button 
                  className={`forecast-btn ${forecastDays === 7 ? 'active' : ''}`}
                  onClick={() => handleHorizonChange(7)}
                >
                  7 Days
                </button>
                <button 
                  className={`forecast-btn ${forecastDays === 14 ? 'active' : ''}`}
                  onClick={() => handleHorizonChange(14)}
                >
                  14 Days
                </button>
              </div>

              {/* View Mode Switcher */}
              <div className="btn-group">
                <button 
                  className={`forecast-btn ${forecastView === 'trend' ? 'active' : ''}`}
                  onClick={() => setForecastView('trend')}
                >
                  📈 Trend & 95% CI
                </button>
                <button 
                  className={`forecast-btn ${forecastView === 'bars' ? 'active' : ''}`}
                  onClick={() => setForecastView('bars')}
                >
                  📊 Inflow vs Outflow
                </button>
                <button 
                  className={`forecast-btn ${forecastView === 'table' ? 'active' : ''}`}
                  onClick={() => setForecastView('table')}
                >
                  📋 Data Table
                </button>
              </div>
            </div>
          </div>

          {/* Mini Summary KPIs inside Forecast Panel */}
          <div className="forecast-mini-kpis">
            <div className="mini-kpi inflow">
              <span>Projected Gross Inflow</span>
              <strong>{money(totalInflow)}</strong>
            </div>
            <div className="mini-kpi outflow">
              <span>Projected Gross Outflow</span>
              <strong>{money(totalOutflow)}</strong>
            </div>
            <div className="mini-kpi net">
              <span>Net Liquidity Forecast</span>
              <strong>{money(netLiquidity)}</strong>
            </div>
            <div className="mini-kpi bound">
              <span>Avg Confidence Margin</span>
              <strong>±{money(avgConfidenceMargin)}</strong>
            </div>
          </div>

          {/* View Mode 1: Interactive SVG Trend Line & Confidence Area Band */}
          {forecastView === 'trend' && (
            <div className="svg-chart-wrap">
              <svg className="chart-svg" viewBox={`0 0 ${svgWidth} ${svgHeight}`} preserveAspectRatio="none">
                {/* Horizontal Grid lines */}
                {[0.25, 0.5, 0.75].map((pct, idx) => {
                  const yVal = paddingY + pct * (svgHeight - 2 * paddingY);
                  return (
                    <line key={idx} x1={paddingX} y1={yVal} x2={svgWidth - paddingX} y2={yVal} className="grid-line" />
                  );
                })}

                {/* 95% Confidence Interval Area Shading */}
                {confidencePath && (
                  <path d={confidencePath} className="confidence-band" />
                )}

                {/* Predicted Cash Flow Trend Line */}
                {trendPath && (
                  <path d={trendPath} className="predicted-line" />
                )}

                {/* Data Points */}
                {forecast.map((p, i) => {
                  const cx = getX(i);
                  const cy = getY(p.predicted_amount);
                  const isActive = activePoint && activePoint.date === p.date;
                  return (
                    <g key={p.date} onClick={() => setActivePoint(p)}>
                      {/* X Axis Label */}
                      <text x={cx} y={svgHeight - 8} className="chart-label">
                        {p.date.slice(5)}
                      </text>
                      {/* Node Dot */}
                      <circle
                        cx={cx}
                        cy={cy}
                        r={isActive ? 6 : 4}
                        className={`chart-dot ${isActive ? 'active' : ''}`}
                        onMouseEnter={() => setActivePoint(p)}
                      />
                    </g>
                  );
                })}
              </svg>

              <div className="chart-legend">
                <div className="legend-item">
                  <span className="legend-line"></span>
                  <span>Predicted Net Inflow</span>
                </div>
                <div className="legend-item">
                  <span className="legend-box ci"></span>
                  <span>95% Confidence Interval [Lower - Upper Bound]</span>
                </div>
              </div>
            </div>
          )}

          {/* View Mode 2: Inflow vs Outflow Visual Bars */}
          {forecastView === 'bars' && (
            <div className="forecast-bars-mode">
              {forecast.map((p) => {
                const maxBarVal = Math.max(...forecast.map(item => Math.max(item.inflow || 0, item.outflow || 0, item.predicted_amount || 0)), 1);
                const inflowH = ((p.inflow || p.predicted_amount * 1.25) / maxBarVal) * 100;
                const outflowH = ((p.outflow || p.predicted_amount * 0.25) / maxBarVal) * 100;
                const netH = (p.predicted_amount / maxBarVal) * 100;
                const isActive = activePoint && activePoint.date === p.date;

                return (
                  <div 
                    key={p.date} 
                    className="bar-col"
                    onMouseEnter={() => setActivePoint(p)}
                    onClick={() => setActivePoint(p)}
                    style={{ opacity: isActive ? 1 : 0.85 }}
                  >
                    <div className="bar-group">
                      <div className="bar-stem inflow" style={{ height: `${Math.max(8, inflowH)}%` }} title={`Inflow: ${money(p.inflow)}`} />
                      <div className="bar-stem net" style={{ height: `${Math.max(8, netH)}%` }} title={`Net: ${money(p.predicted_amount)}`} />
                      <div className="bar-stem outflow" style={{ height: `${Math.max(8, outflowH)}%` }} title={`Outflow: ${money(p.outflow)}`} />
                    </div>
                    <span className="bar-date">{p.date.slice(5)}</span>
                    <span className="bar-val">{money(p.predicted_amount)}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* View Mode 3: Detailed Data Table */}
          {forecastView === 'table' && (
            <div className="table-wrap" style={{ marginTop: '10px' }}>
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Predicted Net Cash Flow</th>
                    <th>Lower Bound (95% CI)</th>
                    <th>Upper Bound (95% CI)</th>
                    <th>Gross Inflow</th>
                    <th>Gross Outflow</th>
                  </tr>
                </thead>
                <tbody>
                  {forecast.map(p => (
                    <tr 
                      key={p.date} 
                      onMouseEnter={() => setActivePoint(p)}
                      style={{ background: activePoint?.date === p.date ? 'rgba(99, 215, 207, 0.08)' : 'transparent' }}
                    >
                      <td><strong>{p.date}</strong></td>
                      <td style={{ color: '#63d7cf', fontWeight: '700' }}>{money(p.predicted_amount)}</td>
                      <td style={{ color: '#8da2c0' }}>{money(p.lower_bound)}</td>
                      <td style={{ color: '#8da2c0' }}>{money(p.upper_bound)}</td>
                      <td style={{ color: '#34d399' }}>{money(p.inflow)}</td>
                      <td style={{ color: '#f87171' }}>{money(p.outflow)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Floating Detail Tooltip Card */}
          {activePoint && (
            <div className="forecast-tooltip">
              <div>
                <span className="tooltip-date">📅 {activePoint.date} Forecast Details</span>
                <span className="muted" style={{ marginLeft: '12px', fontSize: '0.78rem' }}>
                  {new Date(activePoint.date).getDay() === 0 || new Date(activePoint.date).getDay() === 6
                    ? '⚡ Weekend — lower volume learned from history'
                    : '💼 Weekday — standard settlement volume'}
                </span>
              </div>
              <div className="tooltip-items">
                <div className="tooltip-item">
                  <span>Net Predicted:</span>
                  <strong style={{ color: '#63d7cf' }}>{money(activePoint.predicted_amount)}</strong>
                </div>
                <div className="tooltip-item">
                  <span>Confidence Range:</span>
                  <strong>{money(activePoint.lower_bound)} - {money(activePoint.upper_bound)}</strong>
                </div>
                <div className="tooltip-item">
                  <span>Inflow / Outflow:</span>
                  <strong>
                    <span style={{ color: '#34d399' }}>+{money(activePoint.inflow)}</span> / <span style={{ color: '#f87171' }}>-{money(activePoint.outflow)}</span>
                  </strong>
                </div>
              </div>
            </div>
          )}
        </article>

        {/* Settlement windows: the period boundaries reconciliation is scoped to */}
        <article className="panel wide">
          <div className="panel-head">
            <div>
              <h2>Settlement windows ({windows.length})</h2>
              <p className="muted" style={{ fontSize: '0.8rem', margin: '2px 0 0 0' }}>
                Pulled from the Mojaloop settlement API. A closed window is a fixed
                boundary, so reconciling it is a repeatable unit of work.
                {hubStatus && (
                  <span className={`hub-dot ${hubStatus.hub?.reachable ? 'up' : 'down'}`}>
                    {hubStatus.enabled === false
                      ? 'integration disabled'
                      : hubStatus.hub?.reachable
                        ? `hub reachable (${hubStatus.hub.settlement_windows_visible ?? 0} visible)`
                        : 'hub unreachable'}
                  </span>
                )}
              </p>
            </div>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <button className="forecast-btn" onClick={syncWindows}>⟳ Sync windows</button>
              <button
                className="forecast-btn"
                onClick={reconcilePending}
                disabled={busyWindow === 'pending' || !windows.some(w => w.is_reconcilable && !w.reconciled_at)}
              >
                {busyWindow === 'pending' ? 'Working...' : 'Reconcile all pending'}
              </button>
            </div>
          </div>

          {windowNotice && <div className="window-notice">{windowNotice}</div>}

          {windows.length === 0 ? (
            <p className="muted" style={{ fontSize: '0.85rem' }}>
              No settlement windows stored yet. Press <strong>Sync windows</strong> to pull
              them from the hub. This needs <code>MOJALOOP_ENABLED=true</code> and the
              Testing Toolkit running.
            </p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Window</th>
                    <th>State</th>
                    <th>Covers</th>
                    <th>Transactions</th>
                    <th>Reconciled</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {windows.map(w => (
                    <tr
                      key={w.window_id}
                      className={windowFilter === w.window_id ? 'row-active' : ''}
                    >
                      <td><strong>{w.window_id}</strong></td>
                      <td><b className={`tag win-${w.state?.toLowerCase()}`}>{w.state}</b></td>
                      <td className="muted" style={{ fontSize: '0.78rem' }}>
                        {w.scope_from?.slice(0, 16).replace('T', ' ')}
                        {' → '}
                        {w.scope_to?.slice(0, 16).replace('T', ' ')}
                      </td>
                      <td>{w.transactions_in_window}</td>
                      <td>
                        {w.reconciled_at ? (
                          <span className="muted" style={{ fontSize: '0.78rem' }}>
                            run {w.last_run_id} · {w.reconciled_at.slice(0, 16).replace('T', ' ')}
                          </span>
                        ) : w.is_reconcilable ? (
                          <b className="tag partial">pending</b>
                        ) : (
                          <span className="muted" style={{ fontSize: '0.78rem' }}>
                            still open
                          </span>
                        )}
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <button
                          className="link-btn"
                          onClick={() => applyWindowFilter(w.window_id)}
                          title="Filter the results table to this window"
                        >
                          View
                        </button>
                        {w.is_reconcilable && (
                          <button
                            className="link-btn primary"
                            onClick={() => reconcileWindow(w.window_id)}
                            disabled={busyWindow === w.window_id}
                            title="Reconcile only the transactions inside this window"
                          >
                            {busyWindow === w.window_id ? '...' : 'Reconcile'}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {settlements.length > 0 && (
            <div style={{ marginTop: '14px' }}>
              <h3 style={{ fontSize: '0.9rem', color: '#cfe0f5', margin: '0 0 6px 0' }}>
                Settlements ({settlements.length})
              </h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Settlement</th>
                      <th>State</th>
                      <th>Model</th>
                      <th>Windows</th>
                      <th>Net settled</th>
                    </tr>
                  </thead>
                  <tbody>
                    {settlements.map(s => (
                      <tr key={s.settlement_id}>
                        <td><strong>{s.settlement_id}</strong></td>
                        <td><b className={`tag win-${s.state?.toLowerCase()}`}>{s.state}</b></td>
                        <td className="muted" style={{ fontSize: '0.78rem' }}>{s.settlement_model || '—'}</td>
                        <td>{s.window_ids?.join(', ') || '—'}</td>
                        <td>
                          {s.net_amount != null
                            ? `${s.net_amount.toLocaleString()} ${s.currency || ''}`.trim()
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </article>

        {/* Priority Anomalies Section */}
        <article className="panel wide">
          <div className="panel-head">
            <h2>Priority anomalies ({anomalies.length} items)</h2>
            <a 
              href={`${API}/reports/export.csv`} 
              onClick={e => { 
                e.preventDefault(); 
                fetch(`${API}/reports/export.csv`, { headers })
                  .then(r => r.blob())
                  .then(b => { 
                    const u = URL.createObjectURL(b), a = document.createElement('a'); 
                    a.href = u; 
                    a.download = 'reconciliation-report.csv'; 
                    a.click(); 
                    URL.revokeObjectURL(u); 
                  }); 
              }}
            >
              Export CSV
            </a>
          </div>
          {anomalies.map(a => (
            <div className="anomaly" key={a.transaction_id + a.type}>
              <b className={`severity ${a.severity}`}>{a.severity}</b>
              <div>
                <strong>{a.transaction_id} · {a.type.replaceAll('_', ' ')}</strong>
                <p>{a.explanation}</p>
              </div>
              <em title="Computed risk score">{Math.round((a.risk_score || 0) * 100)}/100</em>
            </div>
          ))}
        </article>
      </section>

      {/* Generator Script Viewer Modal */}
      {showScriptModal && generatorCode && (
        <div className="modal-overlay" onClick={() => setShowScriptModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h3 style={{ margin: 0, fontSize: '1.1rem', color: '#f8fafc' }}>📜 Synthetic Data Generator Script (`seed_demo_data`)</h3>
                <span className="muted" style={{ fontSize: '0.8rem', color: '#94a3b8' }}>File: <code>{generatorCode.filename}</code></span>
              </div>
              <button className="close-btn" onClick={() => setShowScriptModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <pre className="code-block">
                <code>{generatorCode.code}</code>
              </pre>
            </div>
            <div className="modal-footer">
              <button onClick={downloadJSON} style={{ background: '#10b981' }}>📥 Download Generated Dataset (JSON)</button>
              <button onClick={() => setShowScriptModal(false)} style={{ background: '#475569' }}>Close</button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
