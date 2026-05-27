import type {
  AppContext, QueryResult, ResponseFormat,
  PKSuggestionsResult, PreviewResult, UploadResult,
} from '../types';

const BASE = '/api';

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // ── Context ──────────────────────────────────────────────────────────
  getContext(): Promise<AppContext> {
    return fetch(`${BASE}/context`).then(r => json<AppContext>(r));
  },

  // ── Table management ─────────────────────────────────────────────────
  confirmTable(table_name: string): Promise<{ success: boolean; error?: string }> {
    return fetch(`${BASE}/table/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ table_name }),
    }).then(r => json(r));
  },

  changeTable(): Promise<{ success: boolean }> {
    return fetch(`${BASE}/table/change`, { method: 'POST' }).then(r => json(r));
  },

  rebuildContext(): Promise<{ success: boolean }> {
    return fetch(`${BASE}/table/rebuild-context`, { method: 'POST' }).then(r => json(r));
  },

  deleteTable(table_name: string): Promise<{ success: boolean; message: string }> {
    return fetch(`${BASE}/table`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ table_name }),
    }).then(r => json(r));
  },

  // ── Query ─────────────────────────────────────────────────────────────
  runQuery(user_query: string, response_format: ResponseFormat): Promise<QueryResult> {
    return fetch(`${BASE}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_query, response_format }),
    }).then(r => json<QueryResult>(r));
  },

  // ── Upload ────────────────────────────────────────────────────────────
  analyzePK(file: File): Promise<PKSuggestionsResult> {
    const fd = new FormData();
    fd.append('file', file);
    return fetch(`${BASE}/upload/analyze-pk`, { method: 'POST', body: fd }).then(r => json(r));
  },

  listTables(): Promise<{ tables: string[] }> {
    return fetch(`${BASE}/upload/tables`).then(r => json(r));
  },

  getPreview(
    file: File,
    opts: { sanitize: boolean; primary_key: string; remove_dups: boolean }
  ): Promise<PreviewResult> {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('sanitize', String(opts.sanitize));
    fd.append('primary_key', opts.primary_key);
    fd.append('remove_dups', String(opts.remove_dups));
    return fetch(`${BASE}/upload/preview`, { method: 'POST', body: fd }).then(r => json(r));
  },

  uploadCSV(
    file: File,
    opts: {
      table_name: string;
      if_exists: string;
      sanitize: boolean;
      primary_key: string;
      remove_dups: boolean;
    }
  ): Promise<UploadResult> {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('table_name', opts.table_name);
    fd.append('if_exists', opts.if_exists);
    fd.append('sanitize', String(opts.sanitize));
    fd.append('primary_key', opts.primary_key);
    fd.append('remove_dups', String(opts.remove_dups));
    return fetch(`${BASE}/upload`, { method: 'POST', body: fd }).then(r => json(r));
  },

  setActiveTable(table_name: string): Promise<{ success: boolean }> {
    return fetch(`${BASE}/table/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ table_name }),
    }).then(r => json(r));
  },
};
