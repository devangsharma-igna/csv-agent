import { useState, useRef, useEffect } from 'react';
import Papa from 'papaparse';
import confetti from 'canvas-confetti';
import { Alert } from './ui/Alert';
import { Button } from './ui/Button';
import { Spinner } from './ui/Spinner';
import { Expander } from './ui/Expander';
import { DataTable } from './ui/DataTable';
import { api } from '../api/client';
import type {
  PKSuggestionsResult,
  PreviewResult,
  ColumnInfo,
} from '../types';
import type { ColumnDef } from '@tanstack/react-table';

interface UploadTabProps {
  onRefresh: () => void;
}

export function UploadTab({ onRefresh }: UploadTabProps) {
  const [file, setFile] = useState<File | null>(null);
  const [fileRows, setFileRows] = useState(0);
  const [fileCols, setFileCols] = useState(0);
  const [fileError, setFileError] = useState('');

  // Step 1
  const [nDups, setNDups] = useState(0);
  const [removeDups, setRemoveDups] = useState(true);

  // Step 2
  const [pkLoading, setPkLoading] = useState(false);
  const [pkSuggestions, setPkSuggestions] = useState<PKSuggestionsResult | null>(null);
  const [pkSelection, setPkSelection] = useState('None (no primary key)');

  // Step 3
  const [tableName, setTableName] = useState('');
  const [sanitize, setSanitize] = useState(false);
  const [ifExists, setIfExists] = useState<'fail' | 'replace' | 'append'>('fail');

  // Step 4 - preview
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Upload
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<{ success: boolean; message: string } | null>(null);
  const [setActiveLoading, setSetActiveLoading] = useState(false);
  const [setActiveMsg, setSetActiveMsg] = useState('');

  // Existing tables
  const [existingTables, setExistingTables] = useState<string[]>([]);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const prevFileName = useRef('');

  // Whenever relevant options change, refresh preview
  useEffect(() => {
    if (!file) return;
    const pk = pkSelection === 'None (no primary key)' ? '' : pkSelection;
    setPreviewLoading(true);
    api.getPreview(file, { sanitize, primary_key: pk, remove_dups: removeDups })
      .then(p => {
        setPreview(p);
        setNDups(p.n_dups);
      })
      .catch(() => setPreview(null))
      .finally(() => setPreviewLoading(false));
  }, [file, sanitize, pkSelection, removeDups]);

  // Load existing tables
  useEffect(() => {
    api.listTables().then(r => setExistingTables(r.tables)).catch(() => {});
  }, []);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null;
    if (!f) return;
    setFileError('');
    setUploadResult(null);
    setSetActiveMsg('');

    // New file: reset PK state
    if (f.name !== prevFileName.current) {
      prevFileName.current = f.name;
      setPkSuggestions(null);
      setPkSelection('None (no primary key)');
      setTableName(suggestTableName(f.name));
    }

    // Parse locally for row/col count
    Papa.parse(f, {
      preview: 2,
      complete: (res) => {
        const cols = (res.data as string[][])[0]?.length ?? 0;
        setFileCols(cols);
        // Full parse for row count
        Papa.parse(f, {
          complete: (full) => setFileRows((full.data as unknown[]).length - 1),
          error: () => setFileRows((res.data as unknown[]).length),
        });
      },
    });
    setFile(f);
  }

  function suggestTableName(filename: string): string {
    return filename
      .replace(/\.[^/.]+$/, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      || 'my_table';
  }

  async function handleAnalyzePK() {
    if (!file) return;
    setPkLoading(true);
    try {
      const sugg = await api.analyzePK(file);
      setPkSuggestions(sugg);
      // Pre-select top suggestion
      if (sugg.suggestions?.[0]?.column) {
        setPkSelection(sugg.suggestions[0].column);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setPkLoading(false);
    }
  }

  async function handleUpload() {
    if (!file || !tableName.trim()) return;
    const pk = pkSelection === 'None (no primary key)' ? '' : pkSelection;

    setUploading(true);
    setUploadResult(null);
    const res = await api.uploadCSV(file, {
      table_name: tableName.trim(),
      if_exists: ifExists,
      sanitize,
      primary_key: pk,
      remove_dups: removeDups,
    });
    setUploading(false);
    setUploadResult(res);
    if (res.success) {
      confetti({ particleCount: 120, spread: 70, origin: { y: 0.6 } });
      api.listTables().then(r => setExistingTables(r.tables)).catch(() => {});
    }
  }

  async function handleSetActive() {
    setSetActiveLoading(true);
    await api.setActiveTable(tableName.trim());
    setSetActiveLoading(false);
    setSetActiveMsg(`Active table set to '${tableName.trim()}'. Head to Query Data!`);
    onRefresh();
  }

  const selectedPkActual = pkSelection === 'None (no primary key)' ? null : pkSelection;

  const tableConflict = Boolean(
    tableName.trim() &&
    existingTables.includes(tableName.trim()) &&
    ifExists === 'fail'
  );

  const colInfoColumns: ColumnDef<ColumnInfo, unknown>[] = [
    { accessorKey: 'Column', header: 'Column' },
    { accessorKey: 'Type', header: 'Type' },
    {
      accessorKey: 'Primary Key',
      header: 'Primary Key',
      cell: info => (info.getValue() ? '✓' : ''),
    },
    { accessorKey: 'Sample value', header: 'Sample value', cell: info => String(info.getValue() ?? '') },
  ];

  const allColumns = file
    ? ['None (no primary key)', ...(preview?.columns ?? [])]
    : ['None (no primary key)'];

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h2 className="text-base font-semibold text-[#0f172a] mb-1">Upload a CSV file</h2>
        <p className="text-xs text-[#6e8ea3]">
          Upload a CSV to create a new table in your database. Once uploaded you can query it
          immediately from the Query Data tab.
        </p>
      </div>

      {/* Step 0: File upload */}
      <div
        className="rounded-[12px] border-2 border-dashed border-[#e2e8f0] bg-[#f8fafc] p-6 text-center hover:border-[#1a56db]/40 transition-colors cursor-pointer"
        onClick={() => fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={handleFileChange}
        />
        <div className="text-3xl mb-2">📂</div>
        <p className="text-sm font-medium text-[#0f172a]">
          {file ? file.name : 'Choose a CSV file'}
        </p>
        {file ? (
          <p className="text-xs text-[#6e8ea3] mt-1">
            {fileRows.toLocaleString()} rows · {fileCols} columns
          </p>
        ) : (
          <p className="text-xs text-[#6e8ea3] mt-1">Maximum recommended size: 50 MB</p>
        )}
        {file && (
          <p className="text-xs text-[#1a56db] mt-2">Click to change file</p>
        )}
      </div>

      {fileError && <Alert variant="error">{fileError}</Alert>}

      {file && (
        <div className="space-y-5">
          {/* Step 1 */}
          <section>
            <h3 className="text-sm font-semibold text-[#0f172a] mb-2">Step 1 — Duplicate Rows</h3>
            {previewLoading ? (
              <Spinner text="Analyzing…" />
            ) : nDups === 0 ? (
              <Alert variant="success">✓ No duplicate rows found.</Alert>
            ) : (
              <div className="space-y-2">
                <Alert variant="warning">
                  <strong>{nDups.toLocaleString()} duplicate rows detected</strong>{' '}
                  ({preview ? ((nDups / preview.n_rows) * 100).toFixed(1) : '?'}% of data).
                  Duplicates are exact row matches across all columns.
                </Alert>
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={removeDups}
                    onChange={e => setRemoveDups(e.target.checked)}
                    className="accent-[#1a56db]"
                  />
                  Remove {nDups.toLocaleString()} duplicate rows before upload
                </label>
                {removeDups && preview ? (
                  <Alert variant="info">
                    After deduplication: <strong>{(preview.n_rows - nDups).toLocaleString()} rows</strong> remain.
                  </Alert>
                ) : (
                  <p className="text-xs text-[#6e8ea3]">Duplicates will be kept in the uploaded table.</p>
                )}
              </div>
            )}
          </section>

          <hr className="border-[#e2e8f0]" />

          {/* Step 2 */}
          <section>
            <h3 className="text-sm font-semibold text-[#0f172a] mb-2">Step 2 — Primary Key</h3>

            <Button variant="secondary" onClick={handleAnalyzePK} loading={pkLoading} size="sm">
              🔍 Analyze columns for primary key
            </Button>

            {pkSuggestions && (
              <div className="mt-3 space-y-2">
                {pkSuggestions.summary && (
                  <Alert variant="info">💡 {pkSuggestions.summary}</Alert>
                )}
                {pkSuggestions.suggestions?.length > 0 ? (
                  <DataTable
                    data={pkSuggestions.suggestions.map(s => ({
                      Column: s.column,
                      Confidence: s.confidence.charAt(0).toUpperCase() + s.confidence.slice(1),
                      Reason: s.reason,
                    }))}
                    columns={[
                      { accessorKey: 'Column', header: 'Column' },
                      { accessorKey: 'Confidence', header: 'Confidence' },
                      { accessorKey: 'Reason', header: 'Reason' },
                    ]}
                  />
                ) : pkSuggestions.composite ? (
                  <Alert variant="info">
                    No single-column PK found. Consider a composite key:{' '}
                    <strong>{pkSuggestions.composite.join(', ')}</strong>
                  </Alert>
                ) : (
                  <Alert variant="warning">No suitable primary key column found in this dataset.</Alert>
                )}
              </div>
            )}

            <div className="mt-3">
              <label className="block text-xs font-medium text-[#0f172a] mb-1">
                Set primary key column
              </label>
              <select
                value={pkSelection}
                onChange={e => setPkSelection(e.target.value)}
                className="w-full rounded-[8px] border border-[#e2e8f0] px-3 py-2 text-sm text-[#0f172a] bg-white focus:outline-none focus:border-[#1a56db]"
              >
                {allColumns.map(col => (
                  <option key={col} value={col}>{col}</option>
                ))}
              </select>
              <p className="text-xs text-[#6e8ea3] mt-1">
                Click 'Analyze columns' above for AI suggestions, or pick any column manually.
              </p>
            </div>

            {/* PK validity shown inline via backend preview (null/dup detection) */}
            {selectedPkActual && preview && (
              <div className="mt-2">
                <Alert variant="success">
                  ✓ <strong>'{preview.preview_pk || selectedPkActual}'</strong> selected as primary key.
                </Alert>
              </div>
            )}
          </section>

          <hr className="border-[#e2e8f0]" />

          {/* Step 3 */}
          <section>
            <h3 className="text-sm font-semibold text-[#0f172a] mb-2">Step 3 — Table Options</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-[#0f172a] mb-1">
                  Table name
                </label>
                <input
                  type="text"
                  value={tableName}
                  onChange={e => setTableName(e.target.value)}
                  className="w-full rounded-[8px] border border-[#e2e8f0] px-3 py-2 text-sm text-[#0f172a] focus:outline-none focus:border-[#1a56db] focus:shadow-[0_0_0_3px_rgba(26,86,219,0.25)]"
                  placeholder="my_table"
                />
                <p className="text-xs text-[#6e8ea3] mt-1">
                  Letters, numbers, and underscores only.
                </p>
              </div>
              <div className="space-y-3">
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={sanitize}
                    onChange={e => setSanitize(e.target.checked)}
                    className="mt-0.5 accent-[#1a56db]"
                  />
                  <div>
                    <span className="text-sm text-[#0f172a]">Sanitize column names</span>
                    <p className="text-xs text-[#6e8ea3]">
                      Replace special chars with underscores. e.g. 'area/location' → 'area_location'
                    </p>
                  </div>
                </label>

                <div>
                  <label className="block text-xs font-medium text-[#0f172a] mb-1">
                    If table already exists
                  </label>
                  <select
                    value={ifExists}
                    onChange={e => setIfExists(e.target.value as 'fail' | 'replace' | 'append')}
                    className="w-full rounded-[8px] border border-[#e2e8f0] px-3 py-2 text-sm text-[#0f172a] bg-white focus:outline-none focus:border-[#1a56db]"
                  >
                    <option value="fail">Abort — keep existing table</option>
                    <option value="replace">Replace — drop and recreate</option>
                    <option value="append">Append — add rows to existing</option>
                  </select>
                </div>
              </div>
            </div>
          </section>

          <hr className="border-[#e2e8f0]" />

          {/* Step 4 */}
          <section>
            <h3 className="text-sm font-semibold text-[#0f172a] mb-2">Step 4 — Preview</h3>

            {previewLoading ? (
              <Spinner text="Loading preview…" />
            ) : preview ? (
              <div className="space-y-3">
                <Expander title="Column schema" defaultOpen={true}>
                  <DataTable<ColumnInfo>
                    data={preview.col_info}
                    columns={colInfoColumns}
                  />
                </Expander>
                <Expander title="Sample data — first 5 rows">
                  <DataTable
                    data={preview.sample_rows}
                    columns={Object.keys(preview.sample_rows[0] ?? {}).map(k => ({
                      accessorKey: k,
                      header: k,
                      cell: (info: { getValue: () => unknown }) => String(info.getValue() ?? ''),
                    }))}
                  />
                </Expander>
              </div>
            ) : null}

            {tableConflict && (
              <Alert variant="warning" className="mt-3">
                A table named <strong>'{tableName.trim()}'</strong> already exists. Change the name
                or switch to <strong>Replace</strong> or <strong>Append</strong>.
              </Alert>
            )}
          </section>

          <hr className="border-[#e2e8f0]" />

          {/* Upload button */}
          <div className="space-y-3">
            <Button
              variant="primary"
              onClick={handleUpload}
              loading={uploading}
              disabled={!tableName.trim() || tableConflict}
              className="w-full py-2.5"
            >
              ⬆️ Upload to Database
            </Button>

            {uploading && <Spinner text={`Uploading rows…`} />}

            {uploadResult && (
              <div className="space-y-2">
                {uploadResult.success ? (
                  <>
                    <Alert variant="success">✅ {uploadResult.message}</Alert>
                    <Alert variant="info">
                      Switch to the <strong>Query Data</strong> tab to start asking questions
                      about <strong>{tableName}</strong>.
                    </Alert>
                    {setActiveMsg ? (
                      <Alert variant="success">{setActiveMsg}</Alert>
                    ) : (
                      <Button
                        variant="secondary"
                        size="sm"
                        loading={setActiveLoading}
                        onClick={handleSetActive}
                      >
                        Set '{tableName}' as active query table
                      </Button>
                    )}
                  </>
                ) : (
                  <Alert variant="error">❌ Upload failed: {uploadResult.message}</Alert>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
