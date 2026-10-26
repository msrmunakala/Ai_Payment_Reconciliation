import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Download, LogOut, Plus, Search, X } from 'lucide-react';
import './styles.css';

const API = import.meta.env.VITE_API_URL ?? '';
const SESSION_KEY = 'recon.analyst.session';

/*
 * Live Mojaloop hub and bank settlement APIs are not connected.
 * File upload is the current ingestion path. When credentials are issued,
 * set them in the environment — never commit real secrets.
 *
 * VITE_MOJALOOP_BASE_URL=
 * VITE_MOJALOOP_API_KEY=
 * VITE_MOJALOOP_CLIENT_ID=
 * VITE_MOJALOOP_CLIENT_SECRET=
 * VITE_MOJALOOP_DFSP_ID=
 * VITE_BANK_SETTLEMENT_BASE_URL=
 * VITE_BANK_SETTLEMENT_API_KEY=
 * VITE_CENTRAL_BANK_RTGS_API_KEY=
 */

const money = (val) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(val || 0);

function authHeaders(session) {
  const headers = {};
  if (session?.api_key) headers['X-API-Key'] = session.api_key;
  if (session?.token) headers.Authorization = `Bearer ${session.token}`;
  return headers;
}

function statusKind(status) {
  if (status === 'matched') return 'matched';
  if (status === 'partial' || status === 'pending_settlement' || status === 'amount_mismatch') return 'partial';
  return 'unmatched';
}

function statusLabel(status) {
  if (status === 'matched') return 'Matched';
  if (status === 'partial' || status === 'amount_mismatch') return 'Partial / Fee discrepancy';
  if (status === 'pending_settlement') return 'Pending settlement';
  if (status === 'unmatched' || status === 'missing_in_erp') return 'Unmatched';
  return (status || '').replaceAll('_', ' ');
}

function Login({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const res = await fetch(`${API}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Sign-in failed');
      if (data.role !== 'analysis_security') throw new Error('This workspace is limited to analysis and security staff.');
      onLogin(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <h1>Ledger vs. Settlement Reconciliation</h1>
        <p className="lede">Sign in as analysis / security staff to upload files and run matching.</p>
        <div className="field">
          <label htmlFor="username">Username</label>
          <input id="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && <div className="login-error">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={busy} style={{ marginTop: 18, width: '100%', justifyContent: 'center' }}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p className="login-hint">Demo analyst: analyst / recon-secure. Live Mojaloop and bank settlement APIs are not connected; use Add files.</p>
      </form>
    </div>
  );
}

function App() {
  const [session, setSession] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null');
    } catch {
      return null;
    }
  });

  const persist = (next) => {
    setSession(next);
    if (next) localStorage.setItem(SESSION_KEY, JSON.stringify(next));
    else localStorage.removeItem(SESSION_KEY);
  };

  if (!session) return <Login onLogin={persist} />;
  return <Dashboard session={session} onLogout={() => persist(null)} />;
}

function Dashboard({ session, onLogout }) {
  const [summary, setSummary] = useState(null);
  const [results, setResults] = useState([]);
  const [anomalies, setAnomalies] = useState([]);
  const [forecast, setForecast] = useState([]);
  const [page, setPage] = useState('ledger');
  const [tableTab, setTableTab] = useState('matched');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toasts, setToasts] = useState([]);

  const headers = useMemo(() => authHeaders(session), [session]);

  const toast = (msg) => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, msg }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3500);
  };

  const load = async () => {
    const read = async (path) => {
      const res = await fetch(`${API}${path}`, { headers });
      if (!res.ok) throw new Error(path);
      return res.json();
    };
    try {
      const [s, r, a] = await Promise.all([
        read('/dashboard/summary'),
        read('/reconciliation/results'),
        read('/anomalies'),
      ]);
      setSummary(s);
      setResults(Array.isArray(r) ? r : []);
      setAnomalies(Array.isArray(a) ? a : []);
    } catch {
      toast('Could not reach the reconciliation service.');
      return;
    }
    try {
      const f = await read('/forecast/?days=7');
      setForecast(Array.isArray(f) ? f : []);
    } catch {
      setForecast([]);
    }
  };

  useEffect(() => { load(); }, []);

  const runMatching = async () => {
    setBusy(true);
    try {
      await fetch(`${API}/reconciliation/run`, { method: 'POST', headers });
      await load();
      toast('Matching complete.');
    } catch {
      toast('Matching failed.');
    } finally {
      setBusy(false);
    }
  };

  const exportCsv = () => {
    fetch(`${API}/reports/export.csv`, { headers })
      .then((r) => r.blob())
      .then((b) => {
        const u = URL.createObjectURL(b);
        const a = document.createElement('a');
        a.href = u;
        a.download = 'ledger-settlement-reconciliation.csv';
        a.click();
        URL.revokeObjectURL(u);
      });
  };

  const q = search.toLowerCase();
  const filtered = results.filter((row) => {
    const kind = statusKind(row.status);
    const tabOk = tableTab === 'matched' ? kind === 'matched' : kind !== 'matched';
    const textOk =
      !q ||
      row.transaction_id.toLowerCase().includes(q) ||
      (row.remarks || '').toLowerCase().includes(q) ||
      (row.ledger_id || '').toLowerCase().includes(q);
    return tabOk && textOk;
  });

  const mismatched = (summary?.mismatched_count ?? 0) || ((summary?.unmatched_transactions || 0) + (summary?.partial_matched || 0));

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Ledger vs. Settlement Reconciliation</h1>
          <div className="sub">Hub transfers compared to bank settlement and ERP invoices</div>
        </div>
        <div className="session">
          <span><strong>{session.display_name}</strong> · Analysis / Security</span>
          <button className="btn btn-ghost" type="button" onClick={onLogout}><LogOut size={14} /> Sign out</button>
        </div>
      </header>

      <div className="toolbar">
        <div className="nav-tabs">
          <button className={page === 'ledger' ? 'active' : ''} onClick={() => setPage('ledger')}>Queue</button>
          <button className={page === 'exceptions' ? 'active' : ''} onClick={() => setPage('exceptions')}>Exceptions</button>
          <button className={page === 'forecast' ? 'active' : ''} onClick={() => setPage('forecast')}>Liquidity</button>
        </div>
        <div className="search">
          <Search size={14} />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter by reference, ledger ID, or variance reason" />
        </div>
        <div className="toolbar-actions">
          <button className="btn" type="button" onClick={() => setUploadOpen(true)}><Plus size={14} /> Add files</button>
          <button className="btn" type="button" onClick={exportCsv}><Download size={14} /> Export CSV</button>
          <button className="btn btn-primary" type="button" onClick={runMatching} disabled={busy}>
            {busy ? 'Running…' : 'Run matching'}
          </button>
        </div>
      </div>

      <main className="main">
        <section className="metrics">
          <article className="metric">
            <div className="label">Total records</div>
            <div className="value">{summary ? summary.total_transactions : '—'}</div>
          </article>
          <article className="metric">
            <div className="label">Matched rate</div>
            <div className="value">{summary ? `${summary.match_rate}%` : '—'}</div>
          </article>
          <article className="metric">
            <div className="label">Reconciled total</div>
            <div className="value">{summary ? money(summary.total_reconciled_value) : '—'}</div>
          </article>
          <article className="metric">
            <div className="label">Mismatched count</div>
            <div className="value">{summary ? mismatched : '—'}</div>
          </article>
        </section>

        {page === 'ledger' && (
          <section className="panel">
            <div className="panel-head">
              <h2>Transaction matching</h2>
              <div className="tabs">
                <button className={tableTab === 'matched' ? 'active' : ''} onClick={() => setTableTab('matched')}>Matched</button>
                <button className={tableTab === 'mismatched' ? 'active' : ''} onClick={() => setTableTab('mismatched')}>Mismatched</button>
              </div>
              <span className="muted">{filtered.length} shown</span>
            </div>
            <ResultTable rows={filtered} onSelect={setSelected} />
          </section>
        )}

        {page === 'exceptions' && (
          <section className="panel">
            <div className="panel-head">
              <h2>Exception queue</h2>
              <span className="muted">{anomalies.length} open</span>
            </div>
            {anomalies.length === 0 ? (
              <div className="empty">No open exceptions.</div>
            ) : (
              anomalies.map((item) => (
                <div className="exception" key={item.transaction_id + item.type}>
                  <span className={`pill ${item.severity === 'high' ? 'unmatched' : 'partial'}`}>{item.severity}</span>
                  <div>
                    <div className="mono">{item.transaction_id}</div>
                    <div style={{ color: 'var(--text-2)', marginTop: 2 }}>{item.explanation}</div>
                  </div>
                  <span className="muted">Risk {item.risk_score}</span>
                </div>
              ))
            )}
          </section>
        )}

        {page === 'forecast' && (
          <section className="panel">
            <div className="panel-head">
              <h2>7-day liquidity forecast</h2>
            </div>
            <div className="forecast-row">
              <div className="forecast-stat">
                <div className="label">Projected inflow</div>
                <div className="value">{money(forecast.reduce((n, p) => n + (p.inflow || 0), 0))}</div>
              </div>
              <div className="forecast-stat">
                <div className="label">Projected outflow</div>
                <div className="value">{money(forecast.reduce((n, p) => n + (p.outflow || 0), 0))}</div>
              </div>
              <div className="forecast-stat">
                <div className="label">Net position</div>
                <div className="value">{money(forecast.reduce((n, p) => n + (p.predicted_amount || 0), 0))}</div>
              </div>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Predicted net</th>
                    <th>Lower bound</th>
                    <th>Upper bound</th>
                  </tr>
                </thead>
                <tbody>
                  {forecast.map((p) => (
                    <tr key={p.date}>
                      <td className="mono">{p.date}</td>
                      <td>{money(p.predicted_amount)}</td>
                      <td>{money(p.lower_bound)}</td>
                      <td>{money(p.upper_bound)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>

      {selected && <Inspect row={selected} onClose={() => setSelected(null)} />}
      {uploadOpen && (
        <UploadModal
          headers={headers}
          onClose={() => setUploadOpen(false)}
          onDone={async (msg) => {
            toast(msg);
            setUploadOpen(false);
            await load();
          }}
        />
      )}
      <div className="toast-wrap">
        {toasts.map((t) => <div className="toast" key={t.id}>{t.msg}</div>)}
      </div>
    </div>
  );
}

function ResultTable({ rows, onSelect }) {
  if (!rows.length) return <div className="empty">No records in this view.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Reference</th>
            <th>Status</th>
            <th>Confidence score</th>
            <th>Variance</th>
            <th>Variance reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const kind = statusKind(row.status);
            const conf = row.match_confidence || 0;
            const band = conf >= 80 ? 'high' : conf >= 50 ? 'mid' : 'low';
            return (
              <tr key={row.transaction_id} onClick={() => onSelect(row)}>
                <td className="mono">{row.transaction_id}</td>
                <td><span className={`pill ${kind}`}>{statusLabel(row.status)}</span></td>
                <td>
                  <div className="score">
                    <div className="score-track"><div className={`score-fill ${band}`} style={{ width: `${conf}%` }} /></div>
                    <span className="mono">{conf}%</span>
                  </div>
                </td>
                <td className="mono">{row.amount_difference ? money(row.amount_difference) : money(0)}</td>
                <td style={{ color: 'var(--text-2)', maxWidth: 360, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {row.remarks || '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Inspect({ row, onClose }) {
  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header>
          <h3>{row.transaction_id}</h3>
          <button className="btn btn-ghost" type="button" onClick={onClose}><X size={16} /></button>
        </header>
        <div className="body">
          <div className="split">
            <div className="box">
              <h4>Ledger</h4>
              <div className="row"><span>Reference</span><strong className="mono">{row.transaction_id}</strong></div>
              <div className="row"><span>Status</span><strong>{statusLabel(row.status)}</strong></div>
              <div className="row"><span>Confidence score</span><strong>{row.match_confidence}%</strong></div>
            </div>
            <div className="box">
              <h4>Settlement</h4>
              <div className="row"><span>Ledger ID</span><strong className="mono">{row.ledger_id || '—'}</strong></div>
              <div className="row"><span>Ledger type</span><strong>{row.ledger_type || '—'}</strong></div>
              <div className="row"><span>Variance</span><strong>{money(row.amount_difference)}</strong></div>
            </div>
          </div>
          <div className="box">
            <h4>Variance reason</h4>
            <p style={{ color: 'var(--text-2)' }}>{row.remarks || 'No note recorded.'}</p>
          </div>
        </div>
        <footer>
          <button className="btn" type="button" onClick={onClose}>Close</button>
        </footer>
      </div>
    </div>
  );
}

function UploadModal({ headers, onClose, onDone }) {
  const [source, setSource] = useState('ledger');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  const upload = async () => {
    const files = inputRef.current?.files;
    if (!files || !files.length) {
      setError('Choose at least one CSV or Excel file.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      let last = null;
      for (const file of files) {
        const body = new FormData();
        body.append('file', file);
        body.append('source', source);
        const res = await fetch(`${API}/transactions/ingest`, { method: 'POST', headers, body });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Upload failed');
        last = data;
      }
      onDone(last?.message || 'Files added.');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header>
          <h3>Add files</h3>
          <button className="btn btn-ghost" type="button" onClick={onClose}><X size={16} /></button>
        </header>
        <div className="body">
          <p style={{ color: 'var(--text-2)', fontSize: 13 }}>
            Live Mojaloop and bank settlement feeds are not available. Upload a ledger extract, bank statement, or ERP file, then run matching.
          </p>
          <div className="source-grid">
            <label><input type="radio" name="source" checked={source === 'ledger'} onChange={() => setSource('ledger')} /> Ledger (hub)</label>
            <label><input type="radio" name="source" checked={source === 'bank'} onChange={() => setSource('bank')} /> Bank settlement</label>
            <label><input type="radio" name="source" checked={source === 'erp'} onChange={() => setSource('erp')} /> ERP invoices</label>
          </div>
          <div className="dropzone">
            CSV or Excel (.csv, .xlsx)
            <br />
            <input ref={inputRef} type="file" accept=".csv,.xlsx,.xls" multiple />
          </div>
          {error && <div className="login-error">{error}</div>}
        </div>
        <footer>
          <button className="btn" type="button" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" type="button" onClick={upload} disabled={busy}>{busy ? 'Uploading…' : 'Upload'}</button>
        </footer>
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
