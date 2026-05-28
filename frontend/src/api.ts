export interface CsvColumn {
  original_name: string;
  sanitized_name: string;
  inferred_type: string;
  nullable: boolean;
  null_count: number;
  sample_values: unknown[];
  unique: boolean;
  pk_candidate_score: number;
}

export interface CsvPreviewResponse {
  preview_id: string;
  suggested_table_name: string;
  row_count: number;
  suggested_pks: string[];
  columns: CsvColumn[];
  preview_rows: Record<string, unknown>[];
}

export interface CommitColumn {
  name: string;
  type: string;
  nullable: boolean;
  null_fill?: unknown | null;
}

export interface CommitRequest {
  preview_id: string;
  table_name: string;
  columns: CommitColumn[];
  primary_keys: string[];
}

export interface QueryResponse {
  status: 'ok' | 'out_of_scope';
  answer?: string;
  figure_b64?: string | null;
  sql?: string;
  row_count?: number;
  parsed?: Record<string, unknown>;
  reason?: string;
}

export interface TablesResponse {
  tables: { name: string; has_context: boolean }[];
}

async function unwrap<T>(resp: Response): Promise<T> {
  // Read the body exactly once. The Fetch API forbids reading it twice — doing
  // resp.json() then resp.text() in a catch yields "body stream already read".
  const text = await resp.text();
  let parsed: any = undefined;
  try { parsed = text ? JSON.parse(text) : undefined; } catch { /* leave parsed undefined */ }

  if (!resp.ok) {
    const detail = parsed?.detail ?? parsed ?? text;
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    (err as any).status = resp.status;
    (err as any).detail = detail;
    throw err;
  }
  return parsed as T;
}

export async function previewCsv(file: File): Promise<CsvPreviewResponse> {
  const fd = new FormData();
  fd.append('file', file);
  return unwrap(await fetch('/api/csv/preview', { method: 'POST', body: fd }));
}

export async function commitCsv(req: CommitRequest): Promise<{ table: string; row_count: number; replaced: boolean; context_path: string }> {
  return unwrap(await fetch('/api/csv/commit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  }));
}

export async function listTables(): Promise<TablesResponse> {
  return unwrap(await fetch('/api/tables'));
}

export async function refreshContext(table: string): Promise<{ ok: true }> {
  return unwrap(await fetch(`/api/tables/${encodeURIComponent(table)}/refresh`, { method: 'POST' }));
}

export interface ContextSummary {
  table: string;
  has_context: boolean;
  exists_in_db: boolean;
  generated_at?: string;
  row_count?: number;
  column_count?: number;
  pk?: string[];
  data_quality_flags?: { column: string; issue: string; detail?: string }[];
  columns?: { name: string; type?: string; semantic?: string; null_pct?: number }[];
}

export async function getContextSummary(table: string): Promise<ContextSummary> {
  return unwrap(await fetch(`/api/tables/${encodeURIComponent(table)}/context`));
}

export async function askQuery(table: string, question: string): Promise<QueryResponse> {
  return unwrap(await fetch('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ table, question }),
  }));
}
