import { useEffect, useState } from 'react';
import Markdown from 'react-markdown';
import {
  askQuery,
  getContextSummary,
  listTables,
  refreshContext,
  type ContextSummary,
} from '../api';

interface Msg {
  role: 'user' | 'assistant' | 'system';
  text: string;
  figure?: string | null;
  sql?: string | null;
  variant?: 'ok' | 'denied' | 'error';
}

export default function QueryPage() {
  const [tables, setTables] = useState<{ name: string; has_context: boolean }[]>([]);
  const [table, setTable] = useState<string>('');
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [ctx, setCtx] = useState<ContextSummary | null>(null);
  const [ctxLoading, setCtxLoading] = useState(false);
  const [ctxExpanded, setCtxExpanded] = useState(false);

  async function loadTables() {
    try {
      const r = await listTables();
      setTables(r.tables);
      if (!table && r.tables.length) setTable(r.tables[0].name);
    } catch (e: any) {
      setMessages(m => [...m, { role: 'system', variant: 'error', text: `Failed to list tables: ${e.message}` }]);
    }
  }

  async function loadContext(t: string) {
    if (!t) { setCtx(null); return; }
    setCtxLoading(true);
    try {
      setCtx(await getContextSummary(t));
    } catch (e: any) {
      setCtx({ table: t, has_context: false, exists_in_db: false });
    } finally {
      setCtxLoading(false);
    }
  }

  useEffect(() => { loadTables(); }, []);

  // When the user switches tables: blow away the chat transcript and
  // re-read the context summary from disk for the newly-selected table.
  useEffect(() => {
    if (!table) { setCtx(null); return; }
    setMessages([]);
    setCtxExpanded(false);
    loadContext(table);
  }, [table]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || !table || busy) return;
    const q = input.trim();
    setInput('');
    setMessages(m => [...m, { role: 'user', text: q }]);
    setBusy(true);
    try {
      const r = await askQuery(table, q);
      if (r.status === 'out_of_scope') {
        setMessages(m => [...m, { role: 'assistant', variant: 'denied', text: `🚫 Out of scope: ${r.reason}` }]);
      } else {
        setMessages(m => [...m, {
          role: 'assistant',
          variant: 'ok',
          text: r.answer || '(no answer)',
          figure: r.figure_b64,
          sql: r.sql,
        }]);
        // If this query built fresh context on the backend, refresh our view.
        if (!ctx?.has_context) loadContext(table);
      }
    } catch (e: any) {
      const detail = e.detail;
      if (detail?.error === 'table_deleted') {
        setMessages(m => [...m, {
          role: 'assistant', variant: 'error',
          text: `🚫 Table '${detail.table}' was deleted (tripped at phase: ${detail.phase}). Reloading table list…`,
        }]);
        loadTables();
        setCtx(null);
      } else {
        setMessages(m => [...m, { role: 'assistant', variant: 'error', text: `Error: ${e.message}` }]);
      }
    } finally { setBusy(false); }
  }

  async function onRefreshContext() {
    if (!table) return;
    setBusy(true);
    try {
      await refreshContext(table);
      setMessages(m => [...m, { role: 'system', text: `Context rebuilt for '${table}'.` }]);
      loadContext(table);
    } catch (e: any) {
      setMessages(m => [...m, { role: 'system', variant: 'error', text: `Refresh failed: ${e.message}` }]);
    } finally { setBusy(false); }
  }

  return (
    <div className="grid grid-rows-[auto_auto_1fr_auto] h-[calc(100vh-120px)] gap-3">
      {/* Row 1: table selector */}
      <div className="flex items-center gap-3 bg-white border rounded px-3 py-2">
        <label className="text-sm font-medium">Table:</label>
        <select
          className="border rounded px-2 py-1 text-sm"
          value={table}
          onChange={e => setTable(e.target.value)}
        >
          {tables.length === 0 && <option value="">(no tables — upload a CSV first)</option>}
          {tables.map(t => (
            <option key={t.name} value={t.name}>
              {t.name}{t.has_context ? ' ✓' : ''}
            </option>
          ))}
        </select>
        <button onClick={loadTables} className="text-xs text-slate-500 hover:text-slate-800">
          refresh list
        </button>
        <button
          onClick={onRefreshContext}
          disabled={!table || busy}
          className="text-xs text-slate-500 hover:text-slate-800 disabled:opacity-50"
        >rebuild context</button>
      </div>

      {/* Row 2: always-visible context status for the active table */}
      <ContextBadge ctx={ctx} loading={ctxLoading} expanded={ctxExpanded} onToggle={() => setCtxExpanded(v => !v)} />

      {/* Row 3: chat transcript */}
      <div className="overflow-y-auto bg-white border rounded p-4 space-y-4">
        {messages.length === 0 && (
          <p className="text-slate-400 text-sm">
            {table ? `Ask any question about ${table}.` : 'Select a table first.'}<br/>
            Examples: <span className="font-mono text-xs">"Top 10 rows by rating"</span>,
            {' '}<span className="font-mono text-xs">"Compare X across categories"</span>
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
            <div className={[
              'max-w-3xl rounded px-3 py-2 text-sm',
              m.role === 'user' && 'bg-blue-600 text-white whitespace-pre-wrap',
              m.role === 'assistant' && m.variant === 'ok' && 'bg-slate-100 text-slate-900',
              m.role === 'assistant' && m.variant === 'denied' && 'bg-amber-50 text-amber-900 border border-amber-200',
              m.role === 'assistant' && m.variant === 'error' && 'bg-red-50 text-red-800 border border-red-200',
              m.role === 'system' && 'bg-slate-50 text-slate-500 italic whitespace-pre-wrap',
            ].filter(Boolean).join(' ')}>
              {m.role === 'assistant' ? (
                <Markdown
                  components={{
                    p:  ({ children }) => <p className="mb-1 last:mb-0">{children}</p>,
                    ul: ({ children }) => <ul className="list-disc pl-5 mb-1 space-y-0.5">{children}</ul>,
                    ol: ({ children }) => <ol className="list-decimal pl-5 mb-1 space-y-0.5">{children}</ol>,
                    li: ({ children }) => <li>{children}</li>,
                    strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                    em: ({ children }) => <em className="italic">{children}</em>,
                    h1: ({ children }) => <h1 className="font-bold text-base mb-1">{children}</h1>,
                    h2: ({ children }) => <h2 className="font-bold mb-1">{children}</h2>,
                    h3: ({ children }) => <h3 className="font-semibold mb-0.5">{children}</h3>,
                    code: ({ children }) => <code className="bg-slate-200 text-slate-800 rounded px-1 font-mono text-xs">{children}</code>,
                    pre: ({ children }) => <pre className="bg-slate-800 text-slate-100 rounded p-2 overflow-x-auto text-xs my-1">{children}</pre>,
                  }}
                >
                  {m.text}
                </Markdown>
              ) : (
                <div>{m.text}</div>
              )}
              {m.figure && (
                <div className="mt-2">
                  <img
                    src={`data:image/png;base64,${m.figure}`}
                    alt="figure"
                    className="rounded border bg-white max-w-full cursor-zoom-in"
                    title="Click to open full screen"
                    onClick={() => {
                      const win = window.open('', '_blank');
                      if (win) {
                        win.document.write(
                          `<html><head><title>Chart</title><style>*{margin:0;padding:0}body{background:#1e1e1e;display:flex;align-items:center;justify-content:center;min-height:100vh}img{max-width:100vw;max-height:100vh;object-fit:contain}</style></head><body><img src="data:image/png;base64,${m.figure}"/></body></html>`
                        );
                        win.document.close();
                      }
                    }}
                  />
                  <p className="text-xs text-slate-400 mt-1">Click chart to open full screen</p>
                </div>
              )}
              {m.sql && (
                <details className="mt-2 text-xs text-slate-500">
                  <summary className="cursor-pointer">SQL</summary>
                  <pre className="bg-slate-900 text-slate-100 p-2 rounded overflow-x-auto">{m.sql}</pre>
                </details>
              )}
            </div>
          </div>
        ))}
        {busy && <p className="text-slate-400 text-sm">Thinking…</p>}
      </div>

      {/* Row 4: input */}
      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          className="flex-1 border rounded px-3 py-2 text-sm"
          placeholder={table ? `Ask about ${table}…` : 'Select a table first'}
          value={input}
          onChange={e => setInput(e.target.value)}
          disabled={!table || busy}
        />
        <button
          type="submit"
          className="bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-2 text-sm disabled:opacity-50"
          disabled={!table || busy || !input.trim()}
        >Send</button>
      </form>
    </div>
  );
}

function ContextBadge({
  ctx, loading, expanded, onToggle,
}: {
  ctx: ContextSummary | null;
  loading: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  if (loading) {
    return <div className="text-xs text-slate-500 px-3 py-2 bg-white border rounded">Checking context…</div>;
  }
  if (!ctx) {
    return <div className="text-xs text-slate-500 px-3 py-2 bg-white border rounded">No table selected.</div>;
  }
  if (!ctx.exists_in_db) {
    return (
      <div className="text-sm px-3 py-2 bg-red-50 border border-red-200 rounded text-red-800">
        ⚠ Table <b>{ctx.table}</b> no longer exists in Supabase.
      </div>
    );
  }
  if (!ctx.has_context) {
    return (
      <div className="text-sm px-3 py-2 bg-amber-50 border border-amber-200 rounded text-amber-900">
        ⚠ No cached context for <b>{ctx.table}</b>. The first query will build it
        (≈10-20s), or click <i>rebuild context</i> above to warm it now.
      </div>
    );
  }

  const ts = ctx.generated_at ? new Date(ctx.generated_at).toLocaleString() : null;
  const flagCount = ctx.data_quality_flags?.length ?? 0;

  return (
    <div className="bg-green-50 border border-green-200 rounded px-3 py-2 text-sm text-green-900">
      <div className="flex items-center gap-4 flex-wrap">
        <span>✓ Context loaded for <b>{ctx.table}</b></span>
        <span className="text-xs text-green-700">
          {ctx.column_count} cols · {ctx.row_count?.toLocaleString() ?? '?'} rows
          {ctx.pk?.length ? ` · PK: ${ctx.pk.join(', ')}` : ' · no PK'}
        </span>
        {ts && <span className="text-xs text-green-700">built {ts}</span>}
        {flagCount > 0 && (
          <span className="text-xs bg-amber-100 text-amber-900 px-1.5 py-0.5 rounded">
            {flagCount} data-quality flag{flagCount > 1 ? 's' : ''}
          </span>
        )}
        <button
          onClick={onToggle}
          className="ml-auto text-xs text-green-700 hover:text-green-900 underline"
        >{expanded ? 'hide details' : 'show details'}</button>
      </div>

      {expanded && (
        <div className="mt-2 text-xs text-slate-700 bg-white border rounded p-2 max-h-56 overflow-y-auto">
          {!!flagCount && (
            <div className="mb-2">
              <div className="font-medium text-amber-900">Data-quality flags</div>
              <ul className="list-disc pl-4">
                {ctx.data_quality_flags!.map((f, i) => (
                  <li key={i}><b>{f.column}</b> — {f.issue}{f.detail ? `: ${f.detail}` : ''}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="font-medium">Columns</div>
          <table className="w-full mt-1">
            <thead>
              <tr className="text-left text-slate-500">
                <th className="pr-2">name</th><th className="pr-2">type</th>
                <th className="pr-2">null %</th><th>semantic</th>
              </tr>
            </thead>
            <tbody>
              {(ctx.columns ?? []).map((c, i) => (
                <tr key={i} className="border-t">
                  <td className="pr-2 font-mono">{c.name}</td>
                  <td className="pr-2">{c.type}</td>
                  <td className="pr-2">{c.null_pct != null ? `${c.null_pct.toFixed(1)}%` : '—'}</td>
                  <td className="text-slate-600">{c.semantic ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
