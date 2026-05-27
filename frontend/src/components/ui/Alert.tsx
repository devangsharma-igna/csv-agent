import React from 'react';

type Variant = 'success' | 'warning' | 'error' | 'info';

const styles: Record<Variant, string> = {
  success: 'bg-green-50 border-green-300 text-green-800',
  warning: 'bg-amber-50 border-amber-300 text-amber-800',
  error:   'bg-red-50 border-red-300 text-red-800',
  info:    'bg-[#eff6ff] border-[#1a56db]/30 text-[#1440a0]',
};

const icons: Record<Variant, string> = {
  success: '✓',
  warning: '⚠',
  error:   '✗',
  info:    'ℹ',
};

interface AlertProps {
  variant: Variant;
  children: React.ReactNode;
  className?: string;
}

export function Alert({ variant, children, className = '' }: AlertProps) {
  return (
    <div
      className={`flex gap-2 rounded-[8px] border px-4 py-3 text-sm ${styles[variant]} ${className}`}
      role="alert"
    >
      <span className="mt-px shrink-0 font-bold">{icons[variant]}</span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
