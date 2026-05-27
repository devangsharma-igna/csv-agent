import { useEffect, useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { QueryTab } from './components/QueryTab';
import { UploadTab } from './components/UploadTab';
import { useAppContext } from './hooks/useAppContext';

type Tab = 'query' | 'upload';

export default function App() {
  const { ctx, refresh } = useAppContext();
  const [activeTab, setActiveTab] = useState<Tab>('query');

  useEffect(() => { refresh(); }, []);

  const tabs: { id: Tab; label: string }[] = [
    { id: 'query', label: '💬 Query Data' },
    { id: 'upload', label: '📤 Upload CSV' },
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-[#f0f4f8]">
      <Sidebar ctx={ctx} onRefresh={refresh} />

      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        {/* App header */}
        <header
          className="shrink-0 px-6 py-4 flex items-center gap-4"
          style={{ background: 'var(--gradient-header)' }}
        >
          <div>
            <h1 className="text-white text-xl font-bold tracking-tight leading-none">
              CSV Agent
            </h1>
            <p className="text-[#76a9fa] text-xs mt-0.5">
              Natural language queries over your data
            </p>
          </div>
        </header>

        {/* Tabs */}
        <div className="shrink-0 bg-white border-b border-[#e2e8f0] px-6">
          <div className="flex gap-1">
            {tabs.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors cursor-pointer ${
                  activeTab === tab.id
                    ? 'border-[#1a56db] text-[#1a56db]'
                    : 'border-transparent text-[#6e8ea3] hover:text-[#475569]'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-6">
          {activeTab === 'query' && <QueryTab ctx={ctx} onRefresh={refresh} />}
          {activeTab === 'upload' && <UploadTab onRefresh={refresh} />}
        </main>
      </div>
    </div>
  );
}
