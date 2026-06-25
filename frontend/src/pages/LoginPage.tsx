import { useState } from 'react';
import { useAuth } from '../auth';

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch {
      setError('Invalid email or password.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-full flex items-center justify-center bg-slate-50 px-6">
      <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4 rounded border bg-white p-6 shadow-sm">
        <div>
          <p className="text-sm font-medium text-blue-700">IGNA Query Agent</p>
          <h1 className="mt-1 text-2xl font-semibold">Sign in</h1>
        </div>
        {error && (
          <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </div>
        )}
        <label className="block text-sm font-medium">
          Email
          <input
            type="email"
            autoComplete="username"
            required
            value={username}
            onChange={event => setUsername(event.target.value)}
            className="mt-1 w-full rounded border px-3 py-2 font-normal"
          />
        </label>
        <label className="block text-sm font-medium">
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={event => setPassword(event.target.value)}
            className="mt-1 w-full rounded border px-3 py-2 font-normal"
          />
        </label>
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  );
}
