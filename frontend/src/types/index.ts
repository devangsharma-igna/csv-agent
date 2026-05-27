export interface AppContext {
  table_name?: string;
  columns?: Array<{ name: string; type: string; [key: string]: unknown }>;
  sample_rows?: Record<string, unknown>[];
  semantic_summary?: string;
}

export type ResponseFormat = 'NL' | 'Figures' | 'NL + Figures';

export interface QueryResult {
  intent: string;
  sql: string;
  rows: Record<string, unknown>[] | null;
  nl_answer: string | null;
  figures: string[] | null;  // Plotly JSON strings
  error: string | null;
  out_of_scope: boolean;
  table_gone: boolean;
}

export interface PKSuggestion {
  column: string;
  confidence: string;
  reason: string;
}

export interface PKSuggestionsResult {
  suggestions: PKSuggestion[];
  composite: string[] | null;
  summary: string;
}

export interface ColumnInfo {
  Column: string;
  Type: string;
  'Primary Key': boolean;
  'Sample value': unknown;
}

export interface PreviewResult {
  n_rows: number;
  n_dups: number;
  columns: string[];
  col_info: ColumnInfo[];
  sample_rows: Record<string, unknown>[];
  preview_pk: string | null;
}

export interface UploadResult {
  success: boolean;
  message: string;
}
