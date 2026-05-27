import { Expander } from './ui/Expander';
import type { QueryResult } from '../types';
import type { AppContext } from '../types';

interface DebugExpanderProps {
  result: QueryResult;
  ctx: AppContext | null;
}

export function DebugExpander({ result, ctx }: DebugExpanderProps) {
  return (
    <Expander title="Debug — pipeline details">
      <div className="space-y-4">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6e8ea3] mb-1">SQL executed</h4>
          <pre className="rounded-[8px] bg-[#0f172a] text-green-400 p-3 text-xs overflow-x-auto">
            <code>{result.sql || '(none)'}</code>
          </pre>
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6e8ea3] mb-1">Detected intent</h4>
          <p className="text-sm text-[#475569]">{result.intent || '(none)'}</p>
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-[#6e8ea3] mb-1">context.json</h4>
          <pre className="rounded-[8px] bg-[#f8fafc] border border-[#e2e8f0] p-3 text-xs overflow-x-auto text-[#475569]">
            {JSON.stringify(ctx, null, 2)}
          </pre>
        </div>
      </div>
    </Expander>
  );
}
