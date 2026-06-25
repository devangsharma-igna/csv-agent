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
  status: 'ok' | 'out_of_scope' | 'confirmation_required';
  answer?: string;
  figure_b64?: string | null;
  sql?: string;
  rows?: Record<string, unknown>[];
  row_count?: number;
  parsed?: Record<string, unknown>;
  reason?: string;
  confirmation_id?: string;
  summary?: string;
  expires_at?: string;
}

export interface WriteResponse {
  status: 'write_ok';
  summary: string;
  rows: Record<string, unknown>[];
  row_count: number;
  table_exists: boolean;
}

export interface TablesResponse {
  tables: { name: string; has_context: boolean }[];
}

export type UserRole = 'user' | 'super_admin';

export interface AuthUser {
  username: string;
  role: UserRole;
}

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

const ERROR_MESSAGES: Record<string, string> = {
  read_only_role: 'Your account has read-only access. Only a Super Admin can modify the database.',
  super_admin_required: 'Only a Super Admin can perform this operation.',
  authentication_required: 'Your session has ended. Please sign in again.',
  confirmation_expired: 'This confirmation has expired. Please submit the request again.',
  invalid_credentials: 'Invalid username or password.',
};

function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const structured = detail as { error?: unknown; message?: unknown };
    if (typeof structured.message === 'string') return structured.message;
    if (typeof structured.error === 'string') {
      return ERROR_MESSAGES[structured.error] ?? structured.error.replaceAll('_', ' ');
    }
  }
  return fallback || 'Request failed.';
}

async function unwrap<T>(resp: Response): Promise<T> {
  // Read the body exactly once. The Fetch API forbids reading it twice — doing
  // resp.json() then resp.text() in a catch yields "body stream already read".
  const text = await resp.text();
  let parsed: any = undefined;
  try { parsed = text ? JSON.parse(text) : undefined; } catch { /* leave parsed undefined */ }

  if (!resp.ok) {
    if (resp.status === 401) {
      onUnauthorized?.();
    }
    const detail = parsed?.detail ?? parsed ?? text;
    const err = new Error(errorMessage(detail, resp.statusText));
    (err as any).status = resp.status;
    (err as any).detail = detail;
    throw err;
  }
  return parsed as T;
}

async function request<T>(input: RequestInfo | URL, init: RequestInit = {}): Promise<T> {
  return unwrap(await fetch(input, {
    ...init,
    credentials: 'include',
  }));
}

export async function getCurrentUser(): Promise<AuthUser> {
  return request('/api/auth/me');
}

export async function login(username: string, password: string): Promise<AuthUser> {
  return request('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
}

export async function logout(): Promise<{ status: 'ok' }> {
  return request('/api/auth/logout', { method: 'POST' });
}

export async function previewCsv(file: File): Promise<CsvPreviewResponse> {
  const fd = new FormData();
  fd.append('file', file);
  return request('/api/csv/preview', { method: 'POST', body: fd });
}

export async function commitCsv(req: CommitRequest): Promise<{ table: string; row_count: number; replaced: boolean; context_path: string }> {
  return request('/api/csv/commit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function listTables(): Promise<TablesResponse> {
  return request('/api/tables');
}

export async function refreshContext(table: string): Promise<{ ok: true }> {
  return request(`/api/tables/${encodeURIComponent(table)}/refresh`, { method: 'POST' });
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
  return request(`/api/tables/${encodeURIComponent(table)}/context`);
}

export async function askQuery(table: string, question: string): Promise<QueryResponse> {
  return request('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ table, question }),
  });
}

export async function confirmQuery(confirmationId: string): Promise<WriteResponse> {
  return request('/api/query/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmation_id: confirmationId }),
  });
}

export async function cancelQuery(confirmationId: string): Promise<{ cancelled: boolean }> {
  return request(`/api/query/pending/${encodeURIComponent(confirmationId)}`, {
    method: 'DELETE',
  });
}
