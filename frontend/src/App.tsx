import { Link, Navigate, NavLink, Route, Routes } from 'react-router-dom';
import { useAuth } from './auth';
import UploadPage from './pages/UploadPage';
import QueryPage from './pages/QueryPage';
import LoginPage from './pages/LoginPage';

export default function App() {
  const { user, loading, logout } = useAuth();

  if (loading) {
    return <div className="min-h-full flex items-center justify-center text-sm text-slate-500">Loading…</div>;
  }
  if (!user) {
    return <LoginPage />;
  }

  const isAdmin = user.role === 'super_admin';

  return (
    <div className="min-h-full flex flex-col">
      <header className="border-b bg-white">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center gap-6">
          <Link to="/" className="font-semibold text-lg">IGNA Query Agent</Link>
          <nav className="flex gap-4 text-sm">
            <NavLink to="/" end className={({ isActive }) =>
              isActive ? 'text-blue-700 font-medium' : 'text-slate-600 hover:text-slate-900'}>
              Query
            </NavLink>
            {isAdmin && (
              <NavLink to="/upload" className={({ isActive }) =>
                isActive ? 'text-blue-700 font-medium' : 'text-slate-600 hover:text-slate-900'}>
                Upload CSV
              </NavLink>
            )}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-xs text-slate-500">
            <span>{user.username}</span>
            <span className="rounded bg-slate-100 px-2 py-1">
              {isAdmin ? 'super admin' : 'user'}
            </span>
            <button onClick={() => void logout()} className="text-slate-600 hover:text-slate-900">
              Log out
            </button>
          </div>
        </div>
      </header>
      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-6">
        <Routes>
          <Route path="/" element={<QueryPage />} />
          <Route path="/upload" element={isAdmin ? <UploadPage /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
