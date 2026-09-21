import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API = 'http://localhost:8000';
const headers = { 'X-API-Key': 'review2-demo-key' };
const money = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value || 0);

function App() {
  const [summary, setSummary] = useState(null);
  const [results, setResults] = useState([]);
  const [anomalies, setAnomalies] = useState([]);
  const [forecast, setForecast] = useState([]);
  const [filter, setFilter] = useState('');
  const [showScriptModal, setShowScriptModal] = useState(false);
  const [generatorCode, setGeneratorCode] = useState(null);
  
  // Forecast interactive state
  const [forecastDays, setForecastDays] = useState(7);
  const [forecastView, setForecastView] = useState('trend'); // 'trend', 'bars', 'table'
  const [activePoint, setActivePoint] = useState(null);

  const fetchForecast = async (days = forecastDays) => {
    try {
      const res = await fetch(`${API}/forecast?days=${days}`, { headers });
      const f = await res.json();
      setForecast(f);
      if (f && f.length > 0) {
        setActivePoint(f[0]);
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

  const load = async () => {
    const [s, r, a] = await Promise.all([
      fetch(`${API}/dashboard/summary`, { headers }).then(x => x.json()),
      fetch(`${API}/reconciliation/results`, { headers }).then(x => x.json()),
      fetch(`${API}/anomalies`, { headers }).then(x => x.json())
    ]);
    setSummary(s);
    setResults(r);
    setAnomalies(a);
    await fetchForecast(forecastDays);
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
            <h2>Reconciliation results ({visible.length} records)</h2>
            <select value={filter} onChange={e => setFilter(e.target.value)}>
              <option value="">All statuses</option>
              {['matched', 'partial', 'unmatched'].map(x => <option key={x}>{x}</option>)}
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
                Machine Learning Time-Series Ridge Regression model with 95% confidence intervals.
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
                    ? '⚡ Weekend volume reduction applied (-35%)' 
                    : '💼 Standard weekday transaction trend'}
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
              <em>{a.risk_score}/100</em>
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
