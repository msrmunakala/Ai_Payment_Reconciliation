import React, { useState } from 'react';
import { EmptyState, Loading } from './ui';

/**
 * Dense data table.
 *
 * Column definition:
 *   {
 *     key,                     unique id
 *     header,                  column label
 *     align: 'right'|'center', monetary and numeric columns right-align
 *     width,                   optional fixed width
 *     mono: true,              tabular figures (ids, amounts, timestamps)
 *     render: (row) => node    cell content
 *   }
 *
 * Supports a sticky header, row hover, click-through on a row, and an optional
 * expandable detail panel per row.
 */
export function DataTable({
  columns,
  rows,
  rowKey,
  loading,
  empty,
  emptyHint,
  onRowClick,
  renderDetail,
  maxHeight,
  compact
}) {
  const [expanded, setExpanded] = useState(null);

  if (loading) return <Loading />;
  if (!rows || rows.length === 0) {
    return <EmptyState title={empty || 'No records'} hint={emptyHint} />;
  }

  const toggle = key => setExpanded(current => (current === key ? null : key));

  return (
    <div
      className={`table-scroll ${compact ? 'table-scroll--compact' : ''}`}
      style={maxHeight ? { maxHeight } : undefined}
    >
      <table className="table">
        <thead>
          <tr>
            {renderDetail && <th className="table__expander" aria-label="Expand" />}
            {columns.map(col => (
              <th
                key={col.key}
                className={col.align ? `is-${col.align}` : undefined}
                style={col.width ? { width: col.width } : undefined}
                scope="col"
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const key = rowKey ? rowKey(row, index) : index;
            const isOpen = expanded === key;
            return (
              <React.Fragment key={key}>
                <tr
                  className={`${onRowClick ? 'is-clickable' : ''} ${isOpen ? 'is-open' : ''}`}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                >
                  {renderDetail && (
                    <td className="table__expander">
                      <button
                        type="button"
                        className="expander"
                        aria-expanded={isOpen}
                        aria-label={isOpen ? 'Collapse row' : 'Expand row'}
                        onClick={event => {
                          event.stopPropagation();
                          toggle(key);
                        }}
                      >
                        {isOpen ? '−' : '+'}
                      </button>
                    </td>
                  )}
                  {columns.map(col => (
                    <td
                      key={col.key}
                      className={`${col.align ? `is-${col.align}` : ''} ${
                        col.mono ? 'is-mono' : ''
                      }`}
                    >
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
                {renderDetail && isOpen && (
                  <tr className="table__detail-row">
                    <td colSpan={columns.length + 1}>
                      <div className="table__detail">{renderDetail(row)}</div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Label/value pairs used inside expanded table rows and detail panels. */
export function DetailGrid({ items }) {
  return (
    <dl className="detail-grid">
      {items
        .filter(item => item)
        .map(([label, value]) => (
          <div key={label} className="detail-grid__item">
            <dt>{label}</dt>
            <dd>{value ?? '—'}</dd>
          </div>
        ))}
    </dl>
  );
}
