import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  commitCsv,
  previewCsv,
  type CsvPreviewResponse,
  type CommitColumn,
} from '../api';

interface ColRow extends CommitColumn {
  original_name: string;
  sample_values: unknown[];
  null_count: number;
  unique: boolean;
  pk_candidate_score: number;
  is_pk: boolean;
}

const PG_TYPES = ['text', 'integer', 'bigint', 'double precision', 'boolean', 'timestamptz', 'date', 'numeric'];

export default function UploadPage() {
  const nav = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvPreviewResponse | null>(null);
  const [tableName, setTableName] = useState('');
  const [cols, setCols] = useState<ColRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [committed, setCommitted] = useState<{ table: string; row_count: number; replaced: boolean } | null>(null);

  async function onPreview(f: File) {
    setBusy(true); setError(null); setCommitted(null);
    try {
      const p = await previewCsv(f);
      setPreview(p);
      setTableName(p.suggested_table_name);
      setCols(p.columns.map(c => ({
        name: c.sanitized_name,
        type: c.inferred_type,
        nullable: c.nullable,
        null_fill: null,
        original_name: c.original_name,
        sample_values: c.sample_values,
        null_count: c.null_count,
        unique: c.unique,
        pk_candidate_score: c.pk_candidate_score,
        is_pk: p.suggested_pks.includes(c.sanitized_name),
      })));
    } catch (e: any) {
      setError(e.message || String(e));
    } finally { setBusy(false); }
  }

  function update(i: number, patch: Partial<ColRow>) {
    setCols(prev => prev.map((c, j) => j === i ? { ...c, ...patch } : c));
  }

  async function onCommit() {
    if (!preview) return;
    setBusy(true); setError(null);
    try {
      const out = await commitCsv({
        preview_id: preview.preview_id,
        table_name: tableName,
        columns: cols.map(c => ({
          name: c.name,
          type: c.type,
          nullable: c.nullable,
          null_fill: c.null_fill ?? null,
        })),
        primary_keys: cols.filter(c => c.is_pk).map(c => c.name),
      });
      setCommitted(out);
      setTimeout(() => nav('/'), 1200);
    } catch (e: any) {
      setError(e.message || String(e));
    } finally { setBusy(false); }
  }

  const hasPk = cols.some(c => c.is_pk);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Upload CSV to Supabase</h1>

      {!preview && (
        <div
          className="border-2 border-dashed rounded-lg p-12 text-center bg-white"
          onDragOver={e => e.preventDefault()}
          onDrop={e => {
            e.preventDefault();
            const f = e.dataTransfer.files?.[0];
            if (f) { setFile(f); onPreview(f); }
          }}
        >
          <p className="text-slate-500 mb-3">Drag a CSV here, or click to choose</p>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={e => {
              const f = e.target.files?.[0];
              if (f) { setFile(f); onPreview(f); }
            }}
            className="block mx-auto"
          />
          {file && <p className="mt-3 text-sm text-slate-600">Selected: {file.name}</p>}
        </div>
      )}

      {busy && <p className="text-slate-500">Working…</p>}
      {error && <div className="border border-red-300 bg-red-50 text-red-800 rounded p-3 text-sm">{error}</div>}
      {committed && (
        <div className="border border-green-300 bg-green-50 text-green-800 rounded p-3 text-sm">
          {committed.replaced ? 'Replaced existing table' : 'Created table'}{' '}
          <b>{committed.table}</b> with <b>{committed.row_count}</b> rows. Redirecting to query page…
        </div>
      )}

      {preview && !committed && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <label className="text-sm font-medium">Table name</label>
            <input
              className="border rounded px-2 py-1 text-sm flex-1 max-w-sm"
              value={tableName}
              onChange={e => setTableName(e.target.value)}
            />
            <span className="text-sm text-slate-500">{preview.row_count} rows in source CSV</span>
          </div>

          {preview.suggested_pks.length > 0 ? (
            <div className="border border-blue-200 bg-blue-50 rounded p-3 text-sm">
              We suggest a primary key on: <b>{preview.suggested_pks.join(', ')}</b>.
              You can edit the selection below or import without a PK.
            </div>
          ) : (
            <div className="border border-amber-200 bg-amber-50 rounded p-3 text-sm">
              No single/composite column qualifies as a primary key. You can still import (no PK).
            </div>
          )}

          <div className="overflow-x-auto bg-white rounded border">
            <table className="text-sm w-full">
              <thead className="bg-slate-100 text-left">
                <tr>
                  <th className="p-2">Original</th>
                  <th className="p-2">Sanitized</th>
                  <th className="p-2">Type</th>
                  <th className="p-2">Nulls</th>
                  <th className="p-2">Null fill</th>
                  <th className="p-2">PK</th>
                  <th className="p-2">Sample</th>
                </tr>
              </thead>
              <tbody>
                {cols.map((c, i) => (
                  <tr key={i} className="border-t">
                    <td className="p-2 text-slate-500">{c.original_name}</td>
                    <td className="p-2">
                      <input
                        className="border rounded px-1 py-0.5 w-40 font-mono text-xs"
                        value={c.name}
                        onChange={e => update(i, { name: e.target.value })}
                      />
                    </td>
                    <td className="p-2">
                      <select
                        className="border rounded px-1 py-0.5 text-xs"
                        value={c.type}
                        onChange={e => update(i, { type: e.target.value })}
                      >
                        {PG_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </td>
                    <td className="p-2 text-slate-600">{c.null_count}</td>
                    <td className="p-2">
                      {c.null_count > 0 ? (
                        <input
                          className="border rounded px-1 py-0.5 w-24 text-xs"
                          placeholder="(leave null)"
                          value={c.null_fill == null ? '' : String(c.null_fill)}
                          onChange={e => update(i, { null_fill: e.target.value === '' ? null : e.target.value })}
                        />
                      ) : <span className="text-slate-400 text-xs">—</span>}
                    </td>
                    <td className="p-2">
                      <input
                        type="checkbox"
                        checked={c.is_pk}
                        onChange={e => update(i, { is_pk: e.target.checked })}
                      />
                    </td>
                    <td className="p-2 text-xs text-slate-500 max-w-xs truncate">
                      {c.sample_values.slice(0, 3).map(v => String(v)).join(', ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="bg-white rounded border p-3">
            <h3 className="text-sm font-medium mb-2">First rows preview</h3>
            <div className="overflow-x-auto">
              <table className="text-xs">
                <thead>
                  <tr>{cols.map((c, i) => <th key={i} className="px-2 py-1 text-left bg-slate-100">{c.name}</th>)}</tr>
                </thead>
                <tbody>
                  {preview.preview_rows.map((r, i) => (
                    <tr key={i}>
                      {cols.map((c, j) => (
                        <td key={j} className="px-2 py-1 border-t">
                          {r[c.original_name as keyof typeof r] != null
                            ? String(r[c.original_name as keyof typeof r])
                            : <span className="text-slate-300">null</span>}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              className="bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-2 text-sm disabled:opacity-50"
              disabled={busy || !tableName}
              onClick={onCommit}
            >
              {busy ? 'Importing…' : `Import to Supabase${hasPk ? '' : ' (no PK)'}`}
            </button>
            <button
              className="text-sm text-slate-500 hover:text-slate-800"
              onClick={() => { setPreview(null); setFile(null); }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
