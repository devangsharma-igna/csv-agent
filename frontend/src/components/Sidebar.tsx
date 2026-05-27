import { useState } from 'react';
import { Button } from './ui/Button';
import { Alert } from './ui/Alert';
import { api } from '../api/client';
import type { AppContext } from '../types';

interface SidebarProps {
  ctx: AppContext | null;
  onRefresh: () => void;
}

export function Sidebar({ ctx, onRefresh }: SidebarProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteMsg, setDeleteMsg] = useState('');
  const [rebuildMsg, setRebuildMsg] = useState('');

  const tableName = ctx?.table_name ?? '';
  const contextBuilt = Boolean(ctx?.columns?.length && ctx?.semantic_summary);
  const colCount = ctx?.columns?.length ?? 0;

  async function handleChangeTable() {
    await api.changeTable();
    onRefresh();
  }

  async function handleRebuild() {
    await api.rebuildContext();
    setRebuildMsg('Context cleared. It will rebuild on your next query.');
    onRefresh();
    setTimeout(() => setRebuildMsg(''), 3000);
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteMsg('');
    const res = await api.deleteTable(tableName);
    setDeleting(false);
    setConfirmDelete(false);
    if (res.success) {
      setDeleteMsg(res.message);
      onRefresh();
    } else {
      setDeleteMsg(`Error: ${res.message}`);
    }
  }

  return (
    <aside className="w-60 shrink-0 bg-white border-r border-[#e2e8f0] flex flex-col">
      {/* Header */}
      <div className="px-4 py-5 border-b border-[#e2e8f0]" style={{ background: 'var(--gradient-header)' }}>
        <h2 className="text-white text-sm font-semibold tracking-wide">Database Table</h2>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {/* Active table status */}
        {tableName ? (
          <div className="rounded-[8px] bg-green-50 border border-green-200 px-3 py-2 text-xs text-green-800">
            <span className="font-semibold">Active table:</span>
            <br />
            <span className="font-mono font-bold">{tableName}</span>
          </div>
        ) : (
          <div className="rounded-[8px] bg-[#eff6ff] border border-[#1a56db]/20 px-3 py-2 text-xs text-[#1440a0]">
            No table configured.
          </div>
        )}

        {/* Context status */}
        {tableName && (
          contextBuilt ? (
            <div className="rounded-[8px] bg-green-50 border border-green-200 px-3 py-2 text-xs text-green-800">
              Context ready ({colCount} columns)
            </div>
          ) : (
            <div className="rounded-[8px] bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
              Context not built yet — will build on first query.
            </div>
          )
        )}

        {rebuildMsg && (
          <div className="rounded-[8px] bg-green-50 border border-green-200 px-3 py-2 text-xs text-green-800">
            {rebuildMsg}
          </div>
        )}

        {/* Actions */}
        {tableName && (
          <div className="space-y-2">
            <Button variant="secondary" size="sm" className="w-full" onClick={handleChangeTable}>
              Change table
            </Button>
            <Button variant="secondary" size="sm" className="w-full" onClick={handleRebuild}>
              Rebuild context
            </Button>
          </div>
        )}

        {/* Danger zone */}
        {tableName && (
          <div className="pt-2">
            <hr className="border-[#e2e8f0] mb-3" />
            <p className="text-[10px] uppercase tracking-widest text-[#6e8ea3] mb-2">Danger zone</p>

            {!confirmDelete ? (
              <Button
                variant="danger"
                size="sm"
                className="w-full"
                onClick={() => setConfirmDelete(true)}
              >
                🗑️ Delete table from DB
              </Button>
            ) : (
              <div className="space-y-2">
                <Alert variant="warning">
                  This will <strong>permanently drop</strong>{' '}
                  <code className="font-mono text-xs">{tableName}</code> from the database.
                  There is no undo.
                </Alert>
                <div className="grid grid-cols-2 gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    loading={deleting}
                    onClick={handleDelete}
                    className="bg-red-600 hover:bg-red-700"
                  >
                    Yes, delete
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setConfirmDelete(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}

            {deleteMsg && (
              <p className="text-xs mt-2 text-[#475569]">{deleteMsg}</p>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
