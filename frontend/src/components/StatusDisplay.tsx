interface Step {
  text: string;
  status: 'pending' | 'running' | 'ok' | 'err';
}

interface StatusDisplayProps {
  steps: Step[];
}

const icons = {
  pending: <span className="text-[#6e8ea3]">⟳</span>,
  running: (
    <svg className="h-3.5 w-3.5 animate-spin text-[#1a56db]" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  ),
  ok:  <span className="text-green-600 font-semibold">✓</span>,
  err: <span className="text-red-500 font-semibold">✗</span>,
};

export function StatusDisplay({ steps }: StatusDisplayProps) {
  return (
    <div className="rounded-[8px] border border-[#e2e8f0] bg-[#f8fafc] px-4 py-3 space-y-1 text-sm">
      {steps.map((step, i) => (
        <div key={i} className="flex items-center gap-2 text-[#475569]">
          <span className="flex h-4 w-4 items-center justify-center shrink-0">
            {icons[step.status]}
          </span>
          <span>{step.text}</span>
        </div>
      ))}
    </div>
  );
}
