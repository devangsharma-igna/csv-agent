function displayValue(value: unknown) {
  if (value == null) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export default function RawDataTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = Array.from(new Set(rows.flatMap(row => Object.keys(row))));

  return (
    <details className="mt-2 text-xs text-slate-500">
      <summary className="cursor-pointer">Raw data</summary>
      {rows.length === 0 ? (
        <p className="mt-1 rounded border bg-white p-2 text-slate-500">No rows returned.</p>
      ) : (
        <div className="mt-1 max-h-72 overflow-auto rounded border bg-white">
          <table className="min-w-full border-collapse text-left">
            <thead>
              <tr>
                {columns.map(column => (
                  <th
                    key={column}
                    className="sticky top-0 z-10 whitespace-nowrap border-b bg-slate-100 px-2 py-1.5 font-medium text-slate-700"
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-b last:border-b-0">
                  {columns.map(column => (
                    <td key={column} className="whitespace-nowrap px-2 py-1.5 align-top text-slate-700">
                      {displayValue(row[column])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
