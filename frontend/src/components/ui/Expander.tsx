import React, { useState } from 'react';

interface ExpanderProps {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export function Expander({ title, defaultOpen = false, children }: ExpanderProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border border-[#e2e8f0] rounded-[8px] overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-[#f8fafc] hover:bg-[#f1f5f9] text-sm font-medium text-[#0f172a] transition-colors text-left"
      >
        <span>{title}</span>
        <svg
          className={`h-4 w-4 text-[#6e8ea3] transition-transform ${open ? 'rotate-180' : ''}`}
          viewBox="0 0 20 20" fill="currentColor"
        >
          <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
        </svg>
      </button>
      {open && (
        <div className="px-4 py-3 border-t border-[#e2e8f0] bg-white">
          {children}
        </div>
      )}
    </div>
  );
}
