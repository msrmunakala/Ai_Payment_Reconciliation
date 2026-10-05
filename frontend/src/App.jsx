import React, { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { NAV_ITEMS, Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { Notice } from './components/ui';
import { useHashRoute } from './lib/useHashRoute';
import { AnomaliesPage } from './pages/AnomaliesPage';
import { DashboardPage } from './pages/DashboardPage';
import { ForecastPage } from './pages/ForecastPage';
import { FxRatesPage } from './pages/FxRatesPage';
import { ReconciliationPage } from './pages/ReconciliationPage';
import { RunHistoryPage } from './pages/RunHistoryPage';
import { SettingsPage } from './pages/SettingsPage';
import { SettlementWindowsPage } from './pages/SettlementWindowsPage';
import { TransactionsPage } from './pages/TransactionsPage';

const TITLES = {
  dashboard: 'Dashboard',
  reconciliation: 'Reconciliation',
  transactions: 'Transactions & Ledgers',
  'settlement-windows': 'Settlement Windows',
  anomalies: 'Exceptions',
  forecast: 'Cash-Flow Forecast',
  'fx-rates': 'FX Rates',
  'run-history': 'Run History',
  settings: 'Settings'
};

/**
 * Application shell.
 *
 * The dashboard summary, service health and scheduler state are loaded once
 * here and passed down, because every page needs the base currency and most
 * need the health banner. Page-specific data is fetched by the page itself so
 * navigating does not refetch everything.
 */
export default function App() {
  const route = useHashRoute();
  const page = NAV_ITEMS.some(item => item.id === route.path) ? route.path : 'dashboard';

  const [summary, setSummary] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [s, webhook, ready, scheduler] = await Promise.all([
        api.summary(),
        api.webhookStatus().catch(() => null),
        api.ready().catch(() => null),
        api.schedulerStatus().catch(() => null)
      ]);
      setSummary(s);
      setHealth({
        ...(webhook || {}),
        ...(webhook?.integration ? { hub: webhook.integration.hub, enabled: webhook.integration.enabled } : {}),
        ready,
        scheduler,
        lastSync: new Date().toISOString()
      });
      setError(null);
    } catch (err) {
      setError(
        err.status === 401
          ? 'Authentication failed. Check the API key configured for this console (VITE_API_KEY).'
          : `Could not reach the reconciliation service at its configured address. ${err.message}`
      );
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const navigate = (path, params) => {
    route.navigate(path, params);
    window.scrollTo({ top: 0 });
  };

  const badges = {
    anomalies: summary?.anomaly_count || 0,
    reconciliation:
      (summary?.flagged_for_review || 0) + (summary?.unmatched_transactions || 0) || 0
  };

  const shared = { summary, health, route, onNavigate: navigate, onRefresh: refresh };

  const renderPage = () => {
    if (error) {
      return (
        <Notice kind="error">
          {error}
        </Notice>
      );
    }
    if (!summary) return <div className="loading">Loading reconciliation data…</div>;

    switch (page) {
      case 'reconciliation':
        return <ReconciliationPage {...shared} />;
      case 'transactions':
        return <TransactionsPage {...shared} />;
      case 'settlement-windows':
        return <SettlementWindowsPage {...shared} />;
      case 'anomalies':
        return <AnomaliesPage {...shared} />;
      case 'forecast':
        return <ForecastPage {...shared} />;
      case 'fx-rates':
        return <FxRatesPage {...shared} />;
      case 'run-history':
        return <RunHistoryPage {...shared} />;
      case 'settings':
        return <SettingsPage {...shared} />;
      default:
        return <DashboardPage {...shared} />;
    }
  };

  return (
    <div className={`app ${collapsed ? 'app--collapsed' : ''}`}>
      <Sidebar
        current={page}
        onNavigate={navigate}
        badges={badges}
        baseCurrency={summary?.base_currency}
        collapsed={collapsed}
        onToggle={() => setCollapsed(c => !c)}
      />

      <div className="app__main">
        <TopBar
          title={TITLES[page]}
          lastRun={summary?.last_run}
          lastSync={health?.lastSync}
          hubConnected={Boolean(health?.hub?.reachable)}
          onRefresh={refresh}
          refreshing={refreshing}
        />
        <main className="app__content">{renderPage()}</main>
      </div>
    </div>
  );
}
