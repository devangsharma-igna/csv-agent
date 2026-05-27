import { useState } from 'react';
import { Alert } from './ui/Alert';
import { Button } from './ui/Button';
import { Spinner } from './ui/Spinner';
import { StatusDisplay } from './StatusDisplay';
import { ResultsDisplay } from './ResultsDisplay';
import { api } from '../api/client';
import type { AppContext, QueryResult } from '../types';

interface QueryTabProps {
  ctx: AppContext | null;
  onRefresh: () => void;
}

type StepStatus = 'pending' | 'running' | 'ok' | 'err';

interface Step {
  text: string;
  status: StepStatus;
}

function buildRunningSteps(tableName: string, contextBuilt: boolean): Step[] {
  return [
    { text: `Checking table '${tableName}'…`, status: 'running' },
    {
      text: contextBuilt
        ? 'Context already built (cached)'
        : 'Building context (first run or after rebuild)…',
      status: 'pending',
    },
    { text: 'Parsing query…', status: 'pending' },
    { text: 'Validating SQL…', status: 'pending' },
    { text: 'Executing…', status: 'pending' },
  ];
}

function buildDoneSteps(tableName: string, result: QueryResult): Step[] {
  if (result.table_gone) {
    return [{ text: `Table '${tableName}' no longer exists — pipeline aborted`, status: 'err' }];
  }
  if (result.out_of_scope) {
    return [
      { text: `Table '${tableName}' verified`, status: 'ok' },
      { text: 'Context ready', status: 'ok' },
      { text: 'Query out of scope — blocked before SQL generation', status: 'err' },
    ];
  }
  if (result.error) {
    return [
      { text: `Table: ${tableName}`, status: 'ok' },
      { text: 'Pipeline error (see below)', status: 'err' },
    ];
  }
  return [
    { text: `Table '${tableName}' verified`, status: 'ok' },
    { text: 'Context ready', status: 'ok' },
    { text: 'Query in scope', status: 'ok' },
    { text: 'Query parsed', status: 'ok' },
    { text: 'SQL validated', status: 'ok' },
    { text: 'Executed', status: 'ok' },
  ];
}

export function QueryTab({ ctx, onRefresh }: QueryTabProps) {
  const [tableInput, setTableInput] = useState('');
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [confirmError, setConfirmError] = useState('');

  const [query, setQuery] = useState('');
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [result, setResult] = useState<QueryResult | null>(null);

  const tableName = ctx?.table_name ?? '';
  const contextBuilt = Boolean(ctx?.columns?.length && ctx?.semantic_summary);

  async function handleConfirm(e: React.FormEvent) {
    e.preventDefault();
    const name = tableInput.trim();
    if (!name) return;
    setConfirmLoading(true);
    setConfirmError('');
    const res = await api.confirmTable(name);
    setConfirmLoading(false);
    if (res.success) {
      onRefresh();
    } else {
      setConfirmError(res.error ?? `Table '${name}' not found.`);
    }
  }

  async function handleRunQuery() {
    if (!query.trim()) return;
    setRunning(true);
    setResult(null);
    setSteps(buildRunningSteps(tableName, contextBuilt));

    const res = await api.runQuery(query.trim(), 'NL + Figures');

    setSteps(buildDoneSteps(tableName, res));
    setResult(res);
    setRunning(false);

    if (res.table_gone) {
      onRefresh();
    }
  }

  if (!tableName) {
    return (
      <div className="max-w-lg space-y-4">
        <Alert variant="warning">
          No table configured. Upload a CSV in the <strong>Upload CSV</strong> tab first, or enter
          an existing table name below.
        </Alert>

        <form onSubmit={handleConfirm} className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-[#0f172a] mb-1">
              Existing table name
            </label>
            <input
              type="text"
              value={tableInput}
              onChange={e => setTableInput(e.target.value)}
              className="w-full rounded-[8px] border border-[#e2e8f0] px-3 py-2 text-sm text-[#0f172a] placeholder-[#6e8ea3] focus:outline-none focus:border-[#1a56db] focus:shadow-[0_0_0_3px_rgba(26,86,219,0.25)]"
              placeholder="e.g. restaurants"
            />
          </div>
          {confirmError && <Alert variant="error">{confirmError}</Alert>}
          <Button variant="primary" type="submit" loading={confirmLoading}>
            Confirm table
          </Button>
        </form>
      </div>
    );
  }

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="space-y-3">
        <div>
          <label className="block text-sm font-medium text-[#0f172a] mb-1">
            Ask a question about your data
          </label>
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !running) handleRunQuery(); }}
            placeholder='e.g. "Show me the top 10 restaurants by rating"'
            className="w-full rounded-[8px] border border-[#e2e8f0] px-3 py-2.5 text-sm text-[#0f172a] placeholder-[#6e8ea3] focus:outline-none focus:border-[#1a56db] focus:shadow-[0_0_0_3px_rgba(26,86,219,0.25)]"
          />
        </div>

        <Button
          variant="primary"
          onClick={handleRunQuery}
          loading={running}
          disabled={!query.trim()}
        >
          Run query
        </Button>
      </div>

      {steps && (
        <StatusDisplay steps={steps} />
      )}

      {running && <Spinner text="Running pipeline…" />}

      {result && !running && (
        <ResultsDisplay
          result={result}
          semanticSummary={ctx?.semantic_summary ?? ''}
        />
      )}
    </div>
  );
}
