import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Alert } from './ui/Alert';
import { Expander } from './ui/Expander';
import { DataTable } from './ui/DataTable';
import { ErrorBoundary } from './ui/ErrorBoundary';
import type { QueryResult } from '../types';
import type { ColumnDef } from '@tanstack/react-table';

// Static import — avoids CJS interop issues with dynamic import()
// eslint-disable-next-line @typescript-eslint/no-require-imports
const Plotly = require('plotly.js') as typeof import('plotly.js');

function PlotlyChart({ figJson }: { figJson: string }) {
  const divRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const el = divRef.current;
    if (!el) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let fig: any;
    try {
      fig = JSON.parse(figJson);
    } catch {
      setError('Failed to parse chart data.');
      return;
    }

    try {
      Plotly.newPlot(
        el,
        fig.data,
        {
          ...fig.layout,
          autosize: true,
          margin: { l: 50, r: 20, t: 40, b: 50 },
          paper_bgcolor: 'white',
          plot_bgcolor: 'white',
          font: { family: "system-ui, 'Segoe UI', Roboto, sans-serif", color: '#475569' },
        },
        { responsive: true, displayModeBar: false },
      );
      // Trigger autosize after Plotly initialises
      window.dispatchEvent(new Event('resize'));
    } catch (e) {
      setError(String(e));
    }

    return () => { Plotly.purge(el); };
  }, [figJson]);

  if (error) return <Alert variant="error">{error}</Alert>;

  return (
    <div
      ref={divRef}
      style={{ width: '100%', minHeight: 320 }}
    />
  );
}

interface ResultsDisplayProps {
  result: QueryResult;
  semanticSummary: string;
}

export function ResultsDisplay({ result, semanticSummary }: ResultsDisplayProps) {
  if (result.table_gone) {
    return (
      <Alert variant="error">
        <strong>Table no longer exists.</strong>
        <br />
        It was dropped while the query was running. The table configuration has been cleared.
        Upload a new CSV or enter a different table name.
      </Alert>
    );
  }

  if (result.out_of_scope) {
    return (
      <Alert variant="warning">
        <strong>Query Out of Scope</strong>
        <br />
        {result.error}
        <br />
        <em className="text-xs opacity-80">
          This table covers: {semanticSummary.slice(0, 200)}…
        </em>
      </Alert>
    );
  }

  if (result.error) {
    return <Alert variant="error">{result.error}</Alert>;
  }

  const rows = result.rows ?? [];

  if (rows.length === 0) {
    return <Alert variant="info">Query executed successfully but returned no rows.</Alert>;
  }

  const columns: ColumnDef<Record<string, unknown>, unknown>[] = Object.keys(rows[0]).map(k => ({
    accessorKey: k,
    header: k,
    cell: info => {
      const v = info.getValue();
      return v === null || v === undefined
        ? <span className="text-[#6e8ea3] italic">null</span>
        : String(v);
    },
  }));

  return (
    <div className="space-y-4">
      {result.nl_answer && (
        <div className="rounded-[12px] border border-[#e2e8f0] bg-white p-5 shadow-[0_4px_12px_rgba(26,86,219,0.12)]">
          <h3 className="mb-3 text-base font-semibold text-[#0f172a]">Answer</h3>
          <div className="prose prose-sm max-w-none text-[#475569]">
            <ReactMarkdown>{result.nl_answer}</ReactMarkdown>
          </div>
        </div>
      )}

      {result.figures != null && (
        <div className="space-y-4">
          {result.figures.length > 0 ? (
            <>
              <h3 className="text-base font-semibold text-[#0f172a]">Charts</h3>
              {result.figures.map((figJson, i) => (
                <ErrorBoundary
                  key={i}
                  fallback={<Alert variant="error">Chart {i + 1} failed to render.</Alert>}
                >
                  <div className="rounded-[12px] border border-[#e2e8f0] bg-white p-4 shadow-[0_4px_12px_rgba(26,86,219,0.12)]">
                    <PlotlyChart figJson={figJson} />
                  </div>
                </ErrorBoundary>
              ))}
            </>
          ) : (
            result.nl_answer
              ? <p className="text-xs text-[#6e8ea3]">No chart could be auto-detected for this result set.</p>
              : <Alert variant="info">No chart could be auto-detected. Try 'NL' or 'NL + Figures' for a text answer.</Alert>
          )}
        </div>
      )}

      <Expander title={`Raw data (${rows.length} rows)`} defaultOpen={false}>
        <DataTable data={rows} columns={columns} />
      </Expander>
    </div>
  );
}
