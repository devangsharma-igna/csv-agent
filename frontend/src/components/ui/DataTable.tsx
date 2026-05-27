import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  type ColumnDef,
} from '@tanstack/react-table';

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  hideIndex?: boolean;
}

export function DataTable<T>({ data, columns }: DataTableProps<T>) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="overflow-x-auto rounded-[8px] border border-[#e2e8f0]">
      <table className="min-w-full text-sm">
        <thead>
          {table.getHeaderGroups().map(hg => (
            <tr key={hg.id} className="bg-[#f8fafc] border-b border-[#e2e8f0]">
              {hg.headers.map(h => (
                <th
                  key={h.id}
                  className="px-3 py-2 text-left font-semibold text-[#0f172a] whitespace-nowrap"
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row, i) => (
            <tr
              key={row.id}
              className={`border-b border-[#e2e8f0] last:border-0 ${i % 2 === 1 ? 'bg-[#f8fafc]' : 'bg-white'}`}
            >
              {row.getVisibleCells().map(cell => (
                <td
                  key={cell.id}
                  className="px-3 py-2 text-[#475569] max-w-[200px] truncate"
                  title={String(cell.getValue() ?? '')}
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {data.length === 0 && (
        <div className="py-6 text-center text-[#6e8ea3] text-sm">No data</div>
      )}
    </div>
  );
}
