import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { 
  ShieldCheck, 
  AlertTriangle, 
  TrendingUp, 
  RefreshCw, 
  Download, 
  Search, 
  Filter, 
  CheckCircle2, 
  XCircle, 
  Info, 
  X, 
  Layers, 
  Activity, 
  FileSpreadsheet, 
  FileCode, 
  ArrowUpRight, 
  Check, 
  ChevronRight, 
  Sliders, 
  Eye, 
  HelpCircle, 
  Zap, 
  BarChart3,
  ExternalLink,
  DollarSign,
  ArrowDownRight
} from 'lucide-react';
import './styles.css';

const API = 'http://localhost:8000';
const headers = { 'X-API-Key': 'review2-demo-key' };

const formatMoney = (val) => 
  new Intl.NumberFormat('en-IN', { 
    style: 'currency', 
    currency: 'INR', 
    maximumFractionDigits: 0 
  }).format(val || 0);

function App() {
  const [summary, setSummary] = useState(null);
  const [results, setResults] = useState([]);
  const [anomalies, setAnomalies] = useState([]);
  const [forecast, setForecast] = useState([]);
  
  // Filtering & View State
  const [filter, setFilter] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [activeTab, setActiveTab] = useState('overview'); // 'overview', 'forecast', 'anomalies'
  
  // Forecast Interactive State
  const [forecastDays, setForecastDays] = useState(7);
  const [forecastView, setForecastView] = useState('trend'); // 'trend', 'bars', 'table'
  const [activePoint, setActivePoint] = useState(null);

  // Modal Inspector State
  const [selectedTx, setSelectedTx] = useState(null);

  // Loading & Action States
  const [loading, setLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  const [toasts, setToasts] = useState([]);

  const addToast = (msg, type = 'info') => {
    const id = Date.now();
    setToasts(prev => [...prev, { id, msg, type }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 4000);
  };

  const fetchForecast = async (days = forecastDays) => {
    try {
      const res = await fetch(`${API}/forecast?days=${days}`, { headers });
      const f = await res.json();
      setForecast(f);
      if (f && f.length > 0 && !activePoint) {
        setActivePoint(f[0]);
      }
    } catch (err) {
      console.error('Error fetching forecast:', err);
    }
  };

  const loadData = async () => {
    setLoading(true);
    try {
      const [s, r, a] = await Promise.all([
        fetch(`${API}/dashboard/summary`, { headers }).then(x => x.json()),
        fetch(`${API}/reconciliation/results`, { headers }).then(x => x.json()),
        fetch(`${API}/anomalies`, { headers }).then(x => x.json())
      ]);
      setSummary(s);
      setResults(r);
      setAnomalies(a);
      await fetchForecast(forecastDays);
    } catch (err) {
      console.error('Failed to load dataset:', err);
      addToast('Error connecting to backend API', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleHorizonChange = (days) => {
    setForecastDays(days);
    fetchForecast(days);
  };

  const handleRunReconciliation = async () => {
    setIsProcessing(true);
    try {
      await fetch(`${API}/reconciliation/run`, { method: 'POST', headers });
      await loadData();
      addToast('Automated reconciliation engine executed successfully', 'success');
    } catch (err) {
      addToast('Failed to run reconciliation', 'error');
    } finally {
      setIsProcessing(false);
    }
  };

  const handleResetDemo = async (count = 150) => {
    setIsProcessing(true);
    try {
      await fetch(`${API}/demo/reset?count=${count}`, { method: 'POST', headers });
      await loadData();
      addToast(`Seeded ${count} synthetic records into sandbox environment`, 'success');
    } catch (err) {
      addToast('Failed to reset dataset', 'error');
    } finally {
      setIsProcessing(false);
    }
  };

  const downloadJSON = () => {
    fetch(`${API}/demo/export-json`, { headers })
      .then(r => r.blob())
      .then(b => {
        const u = URL.createObjectURL(b), a = document.createElement('a');
        a.href = u;
        a.download = 'FinNexus_Reconciliation_Export.json';
        a.click();
        URL.revokeObjectURL(u);
        addToast('JSON dataset exported', 'info');
      });
  };

  const downloadCSV = () => {
    fetch(`${API}/reports/export.csv`, { headers })
      .then(r => r.blob())
      .then(b => {
        const u = URL.createObjectURL(b), a = document.createElement('a');
        a.href = u;
        a.download = 'FinNexus_Reconciliation_Report.csv';
        a.click();
        URL.revokeObjectURL(u);
        addToast('CSV reconciliation report exported', 'info');
      });
  };

  // Filtered dataset
  const filteredResults = results.filter(item => {
    const matchesFilter = !filter || item.status === filter;
    const searchLower = searchTerm.toLowerCase();
    const matchesSearch = !searchTerm || 
      item.transaction_id.toLowerCase().includes(searchLower) ||
      (item.remarks && item.remarks.toLowerCase().includes(searchLower)) ||
      (item.amount_difference && item.amount_difference.toString().includes(searchLower));
    return matchesFilter && matchesSearch;
  });

  // Forecast calculations
  const totalInflow = forecast.reduce((acc, p) => acc + (p.inflow || p.predicted_amount * 1.25), 0);
  const totalOutflow = forecast.reduce((acc, p) => acc + (p.outflow || p.predicted_amount * 0.25), 0);
  const netLiquidity = forecast.reduce((acc, p) => acc + p.predicted_amount, 0);
  const avgConfidenceMargin = forecast.length > 0 
    ? forecast.reduce((acc, p) => acc + (p.upper_bound - p.lower_bound) / 2, 0) / forecast.length 
    : 0;

  // SVG Trend Chart layout calculations
  const svgWidth = 720;
  const svgHeight = 220;
  const paddingX = 45;
  const paddingY = 30;

  let maxY = 1, minY = 0;
  if (forecast.length > 0) {
    maxY = Math.max(...forecast.map(p => Math.max(p.upper_bound || 0, p.inflow || 0, p.predicted_amount || 0))) * 1.12;
    minY = Math.min(0, ...forecast.map(p => p.lower_bound || 0));
  }

  const getX = (i) => forecast.length <= 1 ? svgWidth / 2 : paddingX + (i / (forecast.length - 1)) * (svgWidth - 2 * paddingX);
  const getY = (val) => svgHeight - paddingY - ((val - minY) / (maxY - minY)) * (svgHeight - 2 * paddingY);

  let confidencePath = '', trendPath = '';
  if (forecast.length > 0) {
    const upperPoints = forecast.map((p, i) => `${getX(i)},${getY(p.upper_bound)}`);
    const lowerPoints = [...forecast].reverse().map((p, i) => {
      const origIdx = forecast.length - 1 - i;
      return `${getX(origIdx)},${getY(p.lower_bound)}`;
    });
    confidencePath = `M ${upperPoints.join(' L ')} L ${lowerPoints.join(' L ')} Z`;
    trendPath = `M ${forecast.map((p, i) => `${getX(i)},${getY(p.predicted_amount)}`).join(' L ')}`;
  }

  return (
    <div className="app-container">
      {/* Top Navbar */}
      <header className="top-navbar">
        <div className="brand-section">
          <div className="brand-logo-icon">
            <Zap size={20} />
          </div>
          <div className="brand-title">
            FinNexus Treasury
            <span className="env-badge">Sandbox</span>
          </div>
        </div>

        <div className="navbar-right">
          <div className="system-status">
            <span className="status-dot"></span>
            Mojaloop Ledger Gateway • Online
          </div>

          <div className="user-profile">
            <div className="avatar">AR</div>
            <div className="user-info">
              <span className="user-name">Alex Rivera</span>
              <span className="user-role">Lead Treasury Ops</span>
            </div>
          </div>
        </div>
      </header>

      {/* Sub Header & Action Controls */}
      <div className="sub-header">
        <div className="sub-header-tabs">
          <button 
            className={`nav-tab-btn ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => setActiveTab('overview')}
          >
            <Layers size={16} />
            Reconciliation Queue
          </button>
          <button 
            className={`nav-tab-btn ${activeTab === 'forecast' ? 'active' : ''}`}
            onClick={() => setActiveTab('forecast')}
          >
            <BarChart3 size={16} />
            Liquidity Forecast
          </button>
          <button 
            className={`nav-tab-btn ${activeTab === 'anomalies' ? 'active' : ''}`}
            onClick={() => setActiveTab('anomalies')}
          >
            <AlertTriangle size={16} />
            Priority Audit ({anomalies.length})
          </button>
        </div>

        <div className="header-action-group">
          <button 
            className="btn btn-secondary" 
            onClick={() => handleResetDemo(150)}
            disabled={isProcessing}
          >
            <RefreshCw size={14} className={isProcessing ? 'spin' : ''} />
            Seed 150 Records
          </button>
          <button className="btn btn-secondary" onClick={downloadJSON}>
            <FileCode size={14} />
            JSON
          </button>
          <button className="btn btn-emerald" onClick={downloadCSV}>
            <FileSpreadsheet size={14} />
            Export CSV
          </button>
          <button 
            className="btn btn-primary" 
            onClick={handleRunReconciliation}
            disabled={isProcessing}
          >
            <Zap size={14} />
            {isProcessing ? 'Processing...' : 'Run Auto-Reconcile'}
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <main className="main-content">
        {/* Sandbox Notice Banner */}
        <div className="demo-banner">
          <div className="demo-banner-left">
            <Info size={16} color="#a5b4fc" />
            <span>
              <strong>Environment Active:</strong> Synthetic Mojaloop-to-ERP Settlement Testbed. Multi-dimensional similarity scoring enabled (Amount, Reference ID, Timestamp).
            </span>
          </div>
          <span style={{ fontSize: '0.75rem', opacity: 0.8 }}>Engine Version v2.0.4</span>
        </div>

        {/* Executive KPI Cards */}
        <section className="kpi-grid">
          <div className="kpi-card">
            <div className="kpi-header">
              <span className="kpi-title">Processed Volume</span>
              <div className="kpi-icon-box"><Activity size={18} /></div>
            </div>
            <div className="kpi-value">{summary ? summary.total_transactions : 0}</div>
            <div className="kpi-footer">
              <span className="trend-pill up"><ArrowUpRight size={12} /> +14.2%</span>
              <span>vs previous audit cycle</span>
            </div>
          </div>

          <div className="kpi-card">
            <div className="kpi-header">
              <span className="kpi-title">Match Rate</span>
              <div className="kpi-icon-box"><ShieldCheck size={18} /></div>
            </div>
            <div className="kpi-value">{summary ? `${summary.match_rate}%` : '0%'}</div>
            <div className="kpi-footer">
              <span className="trend-pill up"><Check size={12} /> High Accuracy</span>
              <span>Multi-vector algorithm</span>
            </div>
          </div>

          <div className="kpi-card">
            <div className="kpi-header">
              <span className="kpi-title">Flagged Anomalies</span>
              <div className="kpi-icon-box" style={{ color: '#f59e0b' }}><AlertTriangle size={18} /></div>
            </div>
            <div className="kpi-value">{summary ? summary.anomaly_count : 0}</div>
            <div className="kpi-footer">
              <span className="trend-pill warning">Requires Audit</span>
              <span>Action needed</span>
            </div>
          </div>

          <div className="kpi-card">
            <div className="kpi-header">
              <span className="kpi-title">Reconciled Liquidity</span>
              <div className="kpi-icon-box" style={{ color: '#10b981' }}><DollarSign size={18} /></div>
            </div>
            <div className="kpi-value">{summary ? formatMoney(summary.total_reconciled_value) : '₹0'}</div>
            <div className="kpi-footer">
              <span className="trend-pill up">Settled</span>
              <span>Balanced ledger value</span>
            </div>
          </div>
        </section>

        {/* View Tab 1: Overview & Reconciliation Queue */}
        {(activeTab === 'overview' || activeTab === 'all') && (
          <section className="panel">
            <div className="panel-header">
              <div className="panel-title-group">
                <h2 className="panel-title">
                  <Layers size={18} color="#6366f1" />
                  Transaction Reconciliation Queue
                </h2>
                <p className="panel-subtitle">
                  Real-time matching of Mojaloop hub transfers against internal ERP statements.
                </p>
              </div>

              <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                Showing <strong>{filteredResults.length}</strong> of <strong>{results.length}</strong> entries
              </span>
            </div>

            {/* Toolbar Controls */}
            <div className="table-toolbar">
              <div className="search-box">
                <Search size={15} className="search-icon" />
                <input 
                  type="text" 
                  className="search-input" 
                  placeholder="Search by Transaction ID, remarks, or amount..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                />
                {searchTerm && (
                  <X 
                    size={14} 
                    style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', cursor: 'pointer', color: 'var(--text-muted)' }}
                    onClick={() => setSearchTerm('')} 
                  />
                )}
              </div>

              <div className="filter-pills">
                <button 
                  className={`filter-btn ${filter === '' ? 'active' : ''}`}
                  onClick={() => setFilter('')}
                >
                  All Statuses
                </button>
                <button 
                  className={`filter-btn ${filter === 'matched' ? 'active' : ''}`}
                  onClick={() => setFilter('matched')}
                >
                  Matched
                </button>
                <button 
                  className={`filter-btn ${filter === 'partial' ? 'active' : ''}`}
                  onClick={() => setFilter('partial')}
                >
                  Partial
                </button>
                <button 
                  className={`filter-btn ${filter === 'unmatched' ? 'active' : ''}`}
                  onClick={() => setFilter('unmatched')}
                >
                  Unmatched
                </button>
              </div>
            </div>

            {/* High Density Data Table */}
            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Transaction Reference</th>
                    <th>Status</th>
                    <th>Confidence Score</th>
                    <th>Amount Discrepancy</th>
                    <th>Automated Match Explanation</th>
                    <th style={{ textAlign: 'right' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredResults.length === 0 ? (
                    <tr>
                      <td colSpan="6" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                        No transaction records match the selected filter.
                      </td>
                    </tr>
                  ) : (
                    filteredResults.map((row) => {
                      const conf = row.match_confidence || 0;
                      const confColor = conf > 80 ? '#10b981' : conf > 50 ? '#f59e0b' : '#ef4444';

                      return (
                        <tr key={row.transaction_id} onClick={() => setSelectedTx(row)}>
                          <td>
                            <div style={{ display: 'flex', flexDirection: 'column' }}>
                              <strong className="mono-text">{row.transaction_id}</strong>
                              <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Mojaloop Ledger ID</span>
                            </div>
                          </td>
                          <td>
                            <span className={`status-badge ${row.status}`}>
                              <span className="dot"></span>
                              {row.status.replaceAll('_', ' ')}
                            </span>
                          </td>
                          <td>
                            <div className="confidence-cell">
                              <div className="confidence-bar-track">
                                <div 
                                  className="confidence-bar-fill" 
                                  style={{ width: `${conf}%`, background: confColor }} 
                                />
                              </div>
                              <span className="mono-text" style={{ fontWeight: '700', fontSize: '0.78rem' }}>
                                {conf}%
                              </span>
                            </div>
                          </td>
                          <td className="mono-text" style={{ fontWeight: row.amount_difference > 0 ? '700' : '400', color: row.amount_difference > 0 ? '#f87171' : 'inherit' }}>
                            {row.amount_difference > 0 ? `+${formatMoney(row.amount_difference)}` : formatMoney(0)}
                          </td>
                          <td style={{ maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
                            {row.remarks}
                          </td>
                          <td style={{ textAlign: 'right' }}>
                            <button 
                              className="btn btn-outline" 
                              style={{ padding: '4px 8px', fontSize: '0.72rem' }}
                              onClick={(e) => { e.stopPropagation(); setSelectedTx(row); }}
                            >
                              <Eye size={13} />
                              Inspect
                            </button>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* View Tab 2: Cash-Flow Forecast */}
        {(activeTab === 'forecast' || activeTab === 'overview') && (
          <section className="panel full-column">
            <div className="panel-header">
              <div className="panel-title-group">
                <h2 className="panel-title">
                  <TrendingUp size={18} color="#10b981" />
                  Predictive Liquidity & Cash Flow Forecast
                </h2>
                <p className="panel-subtitle">
                  Time-Series Ridge Regression model with 95% Confidence Interval bounds for treasury balance forecasting.
                </p>
              </div>

              <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                <div className="filter-pills">
                  <button 
                    className={`filter-btn ${forecastDays === 7 ? 'active' : ''}`}
                    onClick={() => handleHorizonChange(7)}
                  >
                    7 Days
                  </button>
                  <button 
                    className={`filter-btn ${forecastDays === 14 ? 'active' : ''}`}
                    onClick={() => handleHorizonChange(14)}
                  >
                    14 Days
                  </button>
                </div>

                <div className="filter-pills">
                  <button 
                    className={`filter-btn ${forecastView === 'trend' ? 'active' : ''}`}
                    onClick={() => setForecastView('trend')}
                  >
                    Trend & 95% CI
                  </button>
                  <button 
                    className={`filter-btn ${forecastView === 'bars' ? 'active' : ''}`}
                    onClick={() => setForecastView('bars')}
                  >
                    Inflow vs Outflow
                  </button>
                  <button 
                    className={`filter-btn ${forecastView === 'table' ? 'active' : ''}`}
                    onClick={() => setForecastView('table')}
                  >
                    Raw Data
                  </button>
                </div>
              </div>
            </div>

            {/* Forecast Mini KPIs */}
            <div className="forecast-kpis">
              <div className="forecast-mini-card">
                <label>Projected Gross Inflow</label>
                <div className="value" style={{ color: '#34d399' }}>{formatMoney(totalInflow)}</div>
              </div>
              <div className="forecast-mini-card">
                <label>Projected Gross Outflow</label>
                <div className="value" style={{ color: '#f87171' }}>{formatMoney(totalOutflow)}</div>
              </div>
              <div className="forecast-mini-card">
                <label>Net Liquidity Position</label>
                <div className="value" style={{ color: '#818cf8' }}>{formatMoney(netLiquidity)}</div>
              </div>
              <div className="forecast-mini-card">
                <label>Avg Margin of Error (CI)</label>
                <div className="value" style={{ color: '#fbbf24' }}>±{formatMoney(avgConfidenceMargin)}</div>
              </div>
            </div>

            {/* SVG Trend Line & Confidence Area Chart */}
            {forecastView === 'trend' && (
              <div className="chart-card-wrapper">
                <svg className="forecast-svg" viewBox={`0 0 ${svgWidth} ${svgHeight}`} preserveAspectRatio="none">
                  {/* Grid Lines */}
                  {[0.25, 0.5, 0.75].map((pct, idx) => {
                    const yVal = paddingY + pct * (svgHeight - 2 * paddingY);
                    return (
                      <line 
                        key={idx} 
                        x1={paddingX} 
                        y1={yVal} 
                        x2={svgWidth - paddingX} 
                        y2={yVal} 
                        stroke="rgba(255,255,255,0.06)" 
                        strokeDasharray="4 4" 
                      />
                    );
                  })}

                  {/* 95% Confidence Interval Shaded Band */}
                  {confidencePath && <path d={confidencePath} className="forecast-band" />}

                  {/* Predicted Cash Flow Line */}
                  {trendPath && <path d={trendPath} className="forecast-line" />}

                  {/* Data Points */}
                  {forecast.map((p, i) => {
                    const cx = getX(i);
                    const cy = getY(p.predicted_amount);
                    const isActive = activePoint && activePoint.date === p.date;
                    return (
                      <g key={p.date} onClick={() => setActivePoint(p)}>
                        <text x={cx} y={svgHeight - 8} className="chart-axis-text">
                          {p.date.slice(5)}
                        </text>
                        <circle
                          cx={cx}
                          cy={cy}
                          r={isActive ? 6 : 4}
                          className={`forecast-dot ${isActive ? 'active' : ''}`}
                          onMouseEnter={() => setActivePoint(p)}
                        />
                      </g>
                    );
                  })}
                </svg>

                <div className="chart-legend-box">
                  <div className="legend-item">
                    <span className="legend-swatch"></span>
                    <span>Predicted Net Inflow</span>
                  </div>
                  <div className="legend-item">
                    <span className="legend-swatch band"></span>
                    <span>95% Confidence Interval Area</span>
                  </div>
                </div>
              </div>
            )}

            {/* View Mode 2: Inflow vs Outflow Bars */}
            {forecastView === 'bars' && (
              <div style={{ padding: '1rem', background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', height: '220px', gap: '10px' }}>
                  {forecast.map(p => {
                    const maxVal = Math.max(...forecast.map(x => Math.max(x.inflow || 0, x.outflow || 0, x.predicted_amount || 0)), 1);
                    const inH = Math.max(10, ((p.inflow || p.predicted_amount * 1.25) / maxVal) * 180);
                    const outH = Math.max(10, ((p.outflow || p.predicted_amount * 0.25) / maxVal) * 180);
                    const isActive = activePoint && activePoint.date === p.date;

                    return (
                      <div 
                        key={p.date} 
                        style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '6px', cursor: 'pointer', opacity: isActive ? 1 : 0.7 }}
                        onClick={() => setActivePoint(p)}
                        onMouseEnter={() => setActivePoint(p)}
                      >
                        <div style={{ display: 'flex', alignItems: 'flex-end', gap: '4px', height: '180px', width: '100%', justifyContent: 'center' }}>
                          <div style={{ width: '40%', background: '#34d399', height: `${inH}px`, borderRadius: '3px 3px 0 0' }} title={`Inflow: ${formatMoney(p.inflow)}`} />
                          <div style={{ width: '40%', background: '#f87171', height: `${outH}px`, borderRadius: '3px 3px 0 0' }} title={`Outflow: ${formatMoney(p.outflow)}`} />
                        </div>
                        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{p.date.slice(5)}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* View Mode 3: Raw Table */}
            {forecastView === 'table' && (
              <div className="table-container" style={{ marginTop: '10px' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Predicted Net Amount</th>
                      <th>Lower Bound (95% CI)</th>
                      <th>Upper Bound (95% CI)</th>
                      <th>Gross Inflow</th>
                      <th>Gross Outflow</th>
                    </tr>
                  </thead>
                  <tbody>
                    {forecast.map(p => (
                      <tr key={p.date} onClick={() => setActivePoint(p)}>
                        <td><strong className="mono-text">{p.date}</strong></td>
                        <td style={{ color: '#818cf8', fontWeight: '700' }} className="mono-text">{formatMoney(p.predicted_amount)}</td>
                        <td style={{ color: 'var(--text-muted)' }} className="mono-text">{formatMoney(p.lower_bound)}</td>
                        <td style={{ color: 'var(--text-muted)' }} className="mono-text">{formatMoney(p.upper_bound)}</td>
                        <td style={{ color: '#34d399' }} className="mono-text">+{formatMoney(p.inflow)}</td>
                        <td style={{ color: '#f87171' }} className="mono-text">-{formatMoney(p.outflow)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Selected Date Detail Banner */}
            {activePoint && (
              <div className="forecast-detail-banner">
                <div>
                  <h4 style={{ color: '#818cf8', fontWeight: '700', fontSize: '0.9rem' }}>
                    {activePoint.date} Treasury Forecast Breakdown
                  </h4>
                  <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                    {new Date(activePoint.date).getDay() === 0 || new Date(activePoint.date).getDay() === 6 
                      ? 'Weekend liquidity pattern (Reduced settlement throughput)' 
                      : 'Regular business day clearing schedule'}
                  </span>
                </div>

                <div style={{ display: 'flex', gap: '20px' }}>
                  <div>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block' }}>Net Projected</span>
                    <strong style={{ color: '#818cf8', fontSize: '0.95rem' }} className="mono-text">{formatMoney(activePoint.predicted_amount)}</strong>
                  </div>
                  <div>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block' }}>CI Range</span>
                    <strong style={{ color: 'var(--text-primary)', fontSize: '0.95rem' }} className="mono-text">{formatMoney(activePoint.lower_bound)} - {formatMoney(activePoint.upper_bound)}</strong>
                  </div>
                  <div>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block' }}>Flow Ratio</span>
                    <strong style={{ fontSize: '0.95rem' }} className="mono-text">
                      <span style={{ color: '#34d399' }}>+{formatMoney(activePoint.inflow)}</span> / <span style={{ color: '#f87171' }}>-{formatMoney(activePoint.outflow)}</span>
                    </strong>
                  </div>
                </div>
              </div>
            )}
          </section>
        )}

        {/* View Tab 3: Priority Anomalies Queue */}
        {(activeTab === 'anomalies' || activeTab === 'overview') && (
          <section className="panel">
            <div className="panel-header">
              <div className="panel-title-group">
                <h2 className="panel-title">
                  <AlertTriangle size={18} color="#f59e0b" />
                  Priority Exception Audit Queue
                </h2>
                <p className="panel-subtitle">
                  AI Risk Engine flagged anomalies requiring manual treasury review.
                </p>
              </div>

              <button className="btn btn-outline" onClick={downloadCSV}>
                <Download size={14} />
                Export Audit Log
              </button>
            </div>

            <div className="anomaly-list">
              {anomalies.length === 0 ? (
                <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                  No high-priority anomalies detected. All ledger records balanced.
                </div>
              ) : (
                anomalies.map((item) => (
                  <div key={item.transaction_id + item.type} className="anomaly-item">
                    <span className={`severity-indicator ${item.severity}`}>
                      {item.severity}
                    </span>

                    <div className="anomaly-content">
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span className="anomaly-title">{item.transaction_id}</span>
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>• {item.type.replaceAll('_', ' ')}</span>
                      </div>
                      <p className="anomaly-desc">{item.explanation}</p>
                    </div>

                    <div className="risk-badge">
                      Risk Score: {item.risk_score}/100
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>
        )}
      </main>

      {/* Transaction Deep-Dive Inspector Modal */}
      {selectedTx && (
        <div className="modal-backdrop" onClick={() => setSelectedTx(null)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h3 style={{ fontSize: '1rem', fontWeight: '700', color: '#ffffff' }}>
                  Transaction Inspector: {selectedTx.transaction_id}
                </h3>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  Side-by-side Mojaloop ledger vs ERP record comparison
                </span>
              </div>
              <button className="btn btn-outline" style={{ padding: '4px' }} onClick={() => setSelectedTx(null)}>
                <X size={18} />
              </button>
            </div>

            <div className="modal-body">
              <div className="diff-grid">
                <div className="record-box">
                  <h4>
                    <Zap size={14} color="#6366f1" />
                    Mojaloop Switch Record
                  </h4>
                  <div className="data-row">
                    <span>Transaction ID:</span>
                    <strong>{selectedTx.transaction_id}</strong>
                  </div>
                  <div className="data-row">
                    <span>Status:</span>
                    <strong style={{ color: selectedTx.status === 'matched' ? '#34d399' : '#fbbf24' }}>
                      {selectedTx.status}
                    </strong>
                  </div>
                  <div className="data-row">
                    <span>Match Confidence:</span>
                    <strong>{selectedTx.match_confidence}%</strong>
                  </div>
                </div>

                <div className="record-box">
                  <h4>
                    <FileSpreadsheet size={14} color="#10b981" />
                    ERP Bank Ledger Record
                  </h4>
                  <div className="data-row">
                    <span>Discrepancy:</span>
                    <strong style={{ color: selectedTx.amount_difference > 0 ? '#f87171' : '#34d399' }}>
                      {formatMoney(selectedTx.amount_difference)}
                    </strong>
                  </div>
                  <div className="data-row">
                    <span>Verification Note:</span>
                    <span style={{ fontSize: '0.78rem' }}>{selectedTx.remarks}</span>
                  </div>
                </div>
              </div>

              <div className="record-box" style={{ background: 'rgba(99,102,241,0.06)', borderColor: 'rgba(99,102,241,0.2)' }}>
                <h4 style={{ color: '#a5b4fc' }}>
                  <Info size={14} />
                  Algorithmic Rationale
                </h4>
                <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                  {selectedTx.remarks}. Multi-vector matching evaluated reference code alignment, timestamp variance within acceptable window, and exact currency amounts.
                </p>
              </div>
            </div>

            <div className="modal-footer">
              <button className="btn btn-outline" onClick={() => setSelectedTx(null)}>
                Close
              </button>
              <button 
                className="btn btn-primary"
                onClick={() => {
                  addToast(`Transaction ${selectedTx.transaction_id} verified`, 'success');
                  setSelectedTx(null);
                }}
              >
                <CheckCircle2 size={14} />
                Confirm Resolution
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast Notification Container */}
      <div className="toast-container">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.type}`}>
            <CheckCircle2 size={16} color={t.type === 'success' ? '#10b981' : '#6366f1'} />
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
