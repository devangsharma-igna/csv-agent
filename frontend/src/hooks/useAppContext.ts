import { useState, useCallback } from 'react';
import { api } from '../api/client';
import type { AppContext } from '../types';

export function useAppContext() {
  const [ctx, setCtx] = useState<AppContext | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getContext();
      setCtx(data);
    } catch {
      setCtx(null);
    } finally {
      setLoading(false);
    }
  }, []);

  return { ctx, loading, refresh, setCtx };
}
