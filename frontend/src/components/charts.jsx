import React, { useState } from 'react';
import { moneyShort, number, dateOnly, percent } from '../lib/format';

/**
 * Charts are plain SVG rather than a charting library: there are only two of
 * them, both simple, and a 400 kB dependency to draw a stacked bar and a line
 * is not a trade a production bundle should make.
 */

/* ------------------------------------------------------------------ */
/* Reconciliation status breakdown                                     */
/* ------------------------------------------------------------------ */

const STATUS_META = [
  { key: 'matched', label: 'Matched', tone: 'ok' },
  { key: 'partial', label: 'Partial', tone: 'warn' },
  { key: 'flagged', label: 'Flagged', tone: 'review' },
  { key: 'unmatched', label: 'Unmatched', tone: 'bad' }
];

/**
 * A single 100% stacked bar plus a legend table. One bar communicates the mix
 * better than four separate charts, and the legend carries the exact counts so
 * the figures stay readable rather than requiring hover.
 */
export function StatusBreakdown({ counts, total }) {
  const sum = total || STATUS_META.reduce((acc, s) => acc + (counts[s.key] || 0), 0);
  if (!sum) return <p className="muted">No reconciliation results yet.</p>;

  let offset = 0;
  const segments = STATUS_META.map(meta => {
    const value = counts[meta.key] || 0;
    const width = (value / sum) * 100;
    const segment = { ...meta, value, width, offset };
    offset += width;
    return segment;
  }).filter(s => s.value > 0);

  return (
    <div className="breakdown">
      <div className="breakdown__bar" role="img" aria-label="Reconciliation status mix">
        {segments.map(seg => (
          <div
            key={seg.key}
            className={`breakdown__seg breakdown__seg--${seg.tone}`}
            style={{ width: `${seg.width}%` }}
            title={`${seg.label}: ${number(seg.value)} (${percent((seg.value / sum) * 100)})`}
          />
        ))}
      </div>

      <table className="breakdown__legend">
        <tbody>
          {STATUS_META.map(meta => {
            const value = counts[meta.key] || 0;
            return (
              <tr key={meta.key}>
                <td>
                  <span className={`swatch swatch--${meta.tone}`} aria-hidden="true" />
                  {meta.label}
                </td>
                <td className="is-right is-mono">{number(value)}</td>
                <td className="is-right is-mono muted">{percent((value / sum) * 100)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td>Total</td>
            <td className="is-right is-mono">{number(sum)}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Cash flow: history + forecast                                       */
/* ------------------------------------------------------------------ */

/**
 * Net cash flow over time, with observed history and forecast on one axis.
 *
 * The forecast is drawn dashed, tinted differently, and separated by a labelled
 * divider, so it is never mistaken for recorded fact. The confidence band is
 * shaded behind the forecast line.
 */
export function CashFlowChart({ history, forecast, currency, model }) {
  const [hover, setHover] = useState(null);

  const historyPoints = (history || []).map(p => ({
    date: p.date,
    net: Number(p.net ?? 0),
    inflow: Number(p.inflow ?? 0),
    outflow: Number(p.outflow ?? 0),
    kind: 'history'
  }));

  const forecastPoints = (forecast || []).map(p => ({
    date: p.date,
    net: Number(p.predicted_amount ?? 0),
    inflow: Number(p.inflow ?? 0),
    outflow: Number(p.outflow ?? 0),
    lower: Number(p.lower_bound ?? 0),
    upper: Number(p.upper_bound ?? 0),
    kind: 'forecast'
  }));

  const points = [...historyPoints, ...forecastPoints];
  if (points.length < 2) {
    return <p className="muted">Not enough cash-flow data to plot.</p>;
  }

  const W = 960;
  const H = 260;
  const padL = 64;
  const padR = 16;
  const padT = 16;
  const padB = 34;

  const values = points.flatMap(p => [p.net, p.upper ?? p.net, p.lower ?? p.net]);
  const maxY = Math.max(...values, 0);
  const minY = Math.min(...values, 0);
  const span = maxY - minY || 1;

  const x = i => padL + (i / (points.length - 1)) * (W - padL - padR);
  const y = v => H - padB - ((v - minY) / span) * (H - padT - padB);

  const line = series =>
    series.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(p.i)} ${y(p.net)}`).join(' ');

  const indexed = points.map((p, i) => ({ ...p, i }));
  const histSeries = indexed.filter(p => p.kind === 'history');
  const foreSeries = indexed.filter(p => p.kind === 'forecast');

  // Join the two lines so there is no visual gap at the boundary.
  const bridge =
    histSeries.length && foreSeries.length
      ? [histSeries[histSeries.length - 1], foreSeries[0]]
      : [];

  const band =
    foreSeries.length > 1
      ? `M ${foreSeries.map(p => `${x(p.i)} ${y(p.upper)}`).join(' L ')} L ${[...foreSeries]
          .reverse()
          .map(p => `${x(p.i)} ${y(p.lower)}`)
          .join(' L ')} Z`
      : '';

  const gridValues = [maxY, minY + span * 0.5, minY].filter(
    (v, i, arr) => arr.indexOf(v) === i
  );
  const boundaryX = foreSeries.length ? x(foreSeries[0].i) : null;

  return (
    <div className="chart">
      <div className="chart__legend">
        <span className="chart__legend-item">
          <span className="chart__key chart__key--history" /> Observed net flow
        </span>
        <span className="chart__legend-item">
          <span className="chart__key chart__key--forecast" /> Forecast
        </span>
        <span className="chart__legend-item">
          <span className="chart__key chart__key--band" /> 95% interval
        </span>
        {model && <span className="chart__legend-note">Forecast model: {model}</span>}
      </div>

      <svg
        className="chart__svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Net cash flow, observed and forecast"
        onMouseLeave={() => setHover(null)}
      >
        {gridValues.map(v => (
          <g key={v}>
            <line className="chart__grid" x1={padL} x2={W - padR} y1={y(v)} y2={y(v)} />
            <text className="chart__axis" x={padL - 8} y={y(v) + 4} textAnchor="end">
              {moneyShort(v, currency)}
            </text>
          </g>
        ))}

        {minY < 0 && (
          <line className="chart__zero" x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} />
        )}

        {band && <path className="chart__band" d={band} />}

        {boundaryX !== null && (
          <>
            <line
              className="chart__boundary"
              x1={boundaryX}
              x2={boundaryX}
              y1={padT}
              y2={H - padB}
            />
            <text className="chart__boundary-label" x={boundaryX + 6} y={padT + 10}>
              forecast →
            </text>
          </>
        )}

        {histSeries.length > 1 && <path className="chart__line" d={line(histSeries)} />}
        {bridge.length === 2 && <path className="chart__line-bridge" d={line(bridge)} />}
        {foreSeries.length > 1 && (
          <path className="chart__line chart__line--forecast" d={line(foreSeries)} />
        )}

        {foreSeries.map(p => (
          <circle
            key={p.date}
            className="chart__dot chart__dot--forecast"
            cx={x(p.i)}
            cy={y(p.net)}
            r={hover?.date === p.date ? 4.5 : 3}
            onMouseEnter={() => setHover(p)}
          />
        ))}

        {/* Invisible hit areas so hovering anywhere in a column works. */}
        {indexed.map(p => (
          <rect
            key={`hit-${p.date}`}
            x={x(p.i) - (W - padL - padR) / points.length / 2}
            y={padT}
            width={(W - padL - padR) / points.length}
            height={H - padT - padB}
            fill="transparent"
            onMouseEnter={() => setHover(p)}
          />
        ))}

        {hover && (
          <line
            className="chart__cursor"
            x1={x(hover.i)}
            x2={x(hover.i)}
            y1={padT}
            y2={H - padB}
          />
        )}

        <text className="chart__axis" x={padL} y={H - 10}>
          {dateOnly(points[0].date)}
        </text>
        <text className="chart__axis" x={W - padR} y={H - 10} textAnchor="end">
          {dateOnly(points[points.length - 1].date)}
        </text>
      </svg>

      <div className="chart__readout">
        {hover ? (
          <>
            <span className="chart__readout-date">
              {dateOnly(hover.date)}
              <em>{hover.kind === 'forecast' ? 'forecast' : 'observed'}</em>
            </span>
            <span>
              Net <strong>{moneyShort(hover.net, currency)}</strong>
            </span>
            <span>
              Inflow <strong className="is-ok">{moneyShort(hover.inflow, currency)}</strong>
            </span>
            <span>
              Outflow <strong className="is-bad">{moneyShort(hover.outflow, currency)}</strong>
            </span>
            {hover.kind === 'forecast' && (
              <span className="muted">
                Range {moneyShort(hover.lower, currency)} – {moneyShort(hover.upper, currency)}
              </span>
            )}
          </>
        ) : (
          <span className="muted">Hover the chart for daily figures.</span>
        )}
      </div>
    </div>
  );
}
