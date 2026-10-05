import { useCallback, useEffect, useState } from 'react';

/**
 * Minimal hash-based routing.
 *
 * Deliberately not a routing library: nine flat pages with optional query
 * parameters do not justify the dependency, and hash routes keep the app
 * deployable as static files behind any web server with no rewrite rules.
 *
 * Route shape:  #/reconciliation?window=9003&status=partial
 */

const parse = () => {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const [path, query] = raw.split('?');
  const params = {};
  new URLSearchParams(query || '').forEach((value, key) => {
    params[key] = value;
  });
  return { path: path || 'dashboard', params };
};

export function useHashRoute() {
  const [route, setRoute] = useState(parse);

  useEffect(() => {
    const onChange = () => setRoute(parse());
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  /** Navigate, optionally carrying query parameters through to the next page. */
  const navigate = useCallback((path, params) => {
    const query = new URLSearchParams(
      Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '')
    ).toString();
    window.location.hash = `#/${path}${query ? `?${query}` : ''}`;
  }, []);

  return { ...route, navigate };
}
