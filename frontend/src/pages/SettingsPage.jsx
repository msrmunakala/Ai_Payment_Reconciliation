import React, { useCallback, useEffect, useState } from 'react';
import { api, API_BASE } from '../api';
import { DetailGrid } from '../components/DataTable';
import {
  Button,
  Card,
  Field,
  FilterBar,
  Notice,
  PageHeader,
  StatusDot,
  TextInput
} from '../components/ui';
import { dateTime, duration, humanise, number, relativeTime } from '../lib/format';

/**
 * Operational settings: the scheduler, the Mojaloop integration, and the
 * environment the console is pointed at. Read-mostly, with the controls the
 * backend already exposes.
 */
export function SettingsPage({ summary, health, onRefresh }) {
  const [scheduler, setScheduler] = useState(null);
  const [hub, setHub] = useState(null);
  const [meta, setMeta] = useState(null);
  const [interval, setIntervalMinutes] = useState('15');
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    const [s, w, root] = await Promise.all([
      api.schedulerStatus().catch(() => null),
      api.webhookStatus().catch(() => null),
      api.root().catch(() => null)
    ]);
    setScheduler(s);
    setHub(w);
    setMeta(root);
    if (s?.interval_minutes) setIntervalMinutes(String(s.interval_minutes));
    else if (s?.configured?.interval_minutes) setIntervalMinutes(String(s.configured.interval_minutes));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (label, fn) => {
    setBusy(label);
    setNotice(null);
    try {
      const res = await fn();
      setNotice({ kind: 'success', text: res?.message || `${label} applied.` });
      await Promise.all([load(), onRefresh()]);
    } catch (err) {
      setNotice({ kind: 'error', text: err.message });
    } finally {
      setBusy(null);
    }
  };

  const running = scheduler?.running;
  const paused = scheduler?.paused;

  return (
    <>
      <PageHeader
        title="Settings"
        subtitle="Scheduling, integration status and environment"
      />

      {notice && (
        <Notice kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Notice>
      )}

      <div className="grid grid--2">
        <Card
          title="Automated reconciliation"
          subtitle="Pulls settlement windows and reconciles those that have newly closed"
        >
          <div className="status-line">
            <StatusDot
              ok={running && !paused}
              warn={paused}
              label={!running ? 'Stopped' : paused ? 'Paused' : 'Running'}
            />
            {scheduler?.next_run_time && (
              <span className="muted">Next run {dateTime(scheduler.next_run_time)}</span>
            )}
          </div>

          <FilterBar>
            <Field label="Interval (minutes)" width="160px">
              <TextInput
                type="number"
                min="1"
                max="1440"
                value={interval}
                onChange={setIntervalMinutes}
              />
            </Field>
            {!running ? (
              <Button
                variant="primary"
                size="sm"
                disabled={busy === 'Start'}
                onClick={() => act('Start', () => api.startScheduler(Number(interval)))}
              >
                Start
              </Button>
            ) : (
              <>
                <Button
                  variant="quiet"
                  size="sm"
                  disabled={busy === 'Interval'}
                  onClick={() => act('Interval', () => api.setSchedulerInterval(Number(interval)))}
                >
                  Apply interval
                </Button>
                {paused ? (
                  <Button
                    variant="quiet"
                    size="sm"
                    disabled={busy === 'Resume'}
                    onClick={() => act('Resume', () => api.resumeScheduler())}
                  >
                    Resume
                  </Button>
                ) : (
                  <Button
                    variant="quiet"
                    size="sm"
                    disabled={busy === 'Pause'}
                    onClick={() => act('Pause', () => api.pauseScheduler())}
                  >
                    Pause
                  </Button>
                )}
                <Button
                  variant="quiet"
                  size="sm"
                  disabled={busy === 'Stop'}
                  onClick={() => act('Stop', () => api.stopScheduler())}
                >
                  Stop
                </Button>
              </>
            )}
            <Button
              variant="quiet"
              size="sm"
              disabled={busy === 'Run now'}
              onClick={() => act('Run now', () => api.runSchedulerNow({ sync_mojaloop: true }))}
            >
              Run cycle now
            </Button>
          </FilterBar>

          <DetailGrid
            items={[
              ['Enabled at startup', scheduler?.configured?.enabled_on_startup ? 'Yes' : 'No'],
              [
                'Configured interval',
                scheduler?.configured?.interval_minutes
                  ? `${scheduler.configured.interval_minutes} min`
                  : '—'
              ],
              [
                'Syncs settlement windows',
                scheduler?.configured?.sync_mojaloop ? 'Yes' : 'No'
              ],
              [
                'Refreshes forecast',
                scheduler?.configured?.refresh_forecast ? 'Yes' : 'No'
              ],
              ['Cycle in progress', scheduler?.cycle_in_progress ? 'Yes' : 'No'],
              [
                'Last cycle',
                scheduler?.last_result
                  ? `${humanise(scheduler.last_result.trigger)} · ${
                      scheduler.last_result.duration_seconds != null
                        ? duration(scheduler.last_result.duration_seconds)
                        : '—'
                    }`
                  : 'None yet'
              ]
            ]}
          />

          {scheduler?.note && <p className="muted small">{scheduler.note}</p>}
        </Card>

        <Card title="Mojaloop integration">
          <div className="status-line">
            <StatusDot
              ok={hub?.integration?.hub?.reachable}
              label={
                hub?.integration?.enabled === false
                  ? 'Disabled'
                  : hub?.integration?.hub?.reachable
                    ? 'Connected'
                    : 'Unreachable'
              }
            />
            {hub?.integration?.hub?.latency_ms != null && (
              <span className="muted">{Math.round(hub.integration.hub.latency_ms)} ms</span>
            )}
          </div>

          <DetailGrid
            items={[
              ['Transfer API', hub?.integration?.base_url],
              ['Settlement API', hub?.integration?.settlement_base_url],
              ['FSP identifier', hub?.integration?.fsp_id],
              ['FSPIOP version', hub?.integration?.fspiop_version],
              ['Request timeout', hub?.integration?.timeout_seconds ? `${hub.integration.timeout_seconds}s` : '—'],
              ['Max retries', hub?.integration?.max_retries],
              [
                'Simulator fallback',
                hub?.integration?.fallback_to_simulator ? 'Enabled' : 'Disabled'
              ],
              [
                'Windows visible',
                hub?.integration?.hub?.settlement_windows_visible != null
                  ? number(hub.integration.hub.settlement_windows_visible)
                  : '—'
              ]
            ]}
          />

          {!hub?.signature_verification && (
            <Notice kind="warn">
              Webhook signature verification is off because no <code>WEBHOOK_SECRET</code> is
              set. The Testing Toolkit cannot sign its callbacks, so this is expected while it
              drives the backend — but set a secret before exposing the webhook.
            </Notice>
          )}

          <div className="subsection">
            <Button
              variant="quiet"
              size="sm"
              disabled={busy === 'Sync windows'}
              onClick={() => act('Sync windows', () => api.syncWindows())}
            >
              Sync settlement windows
            </Button>
          </div>
        </Card>
      </div>

      <div className="grid grid--2">
        <Card title="Environment">
          <DetailGrid
            items={[
              ['Console API base', API_BASE],
              ['Service', meta?.project],
              ['Version', meta?.version],
              ['Environment', meta?.environment],
              ['Base currency', meta?.base_currency || summary?.base_currency],
              ['Database', health?.ready?.database],
              ['Readiness', health?.ready?.status],
              ['Last refreshed', health?.lastSync ? relativeTime(health.lastSync) : '—']
            ]}
          />
          {meta?.capabilities && (
            <div className="subsection">
              <h3 className="subsection__title">Capabilities reported by the service</h3>
              <ul className="notes">
                {meta.capabilities.map(cap => (
                  <li key={cap}>{cap}</li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <Card
          title="Data management"
          subtitle="Synthetic demo data only. These actions do not touch hub records."
        >
          <p className="muted small">{summary?.synthetic_data_notice}</p>

          <div className="subsection">
            <FilterBar>
              <Button variant="quiet" size="sm" onClick={() => api.exportCsv()}>
                Export reconciliation CSV
              </Button>
              <Button variant="quiet" size="sm" onClick={() => api.exportJson()}>
                Export dataset JSON
              </Button>
            </FilterBar>
          </div>

          <Notice kind="warn">
            Regenerating the demo dataset deletes the current synthetic transactions, bank
            lines, ERP invoices and reconciliation results. It is intended for demonstrations
            only.
          </Notice>
          <Button
            variant="danger"
            size="sm"
            disabled={busy === 'Regenerate'}
            onClick={() => {
              if (
                !window.confirm(
                  'Delete the current synthetic dataset and regenerate it? Reconciliation results and exception triage will be lost.'
                )
              ) {
                return;
              }
              act('Regenerate', () =>
                api.resetDemo({ count: 600, seed: 42, history_days: 30 })
              );
            }}
          >
            Regenerate demo dataset
          </Button>
        </Card>
      </div>
    </>
  );
}
