import { Link, NavLink, Route, Routes } from 'react-router-dom';
import UploadPage from './pages/UploadPage';
import QueryPage from './pages/QueryPage';

export default function App() {
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
            <NavLink to="/upload" className={({ isActive }) =>
              isActive ? 'text-blue-700 font-medium' : 'text-slate-600 hover:text-slate-900'}>
              Upload CSV
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-6">
        <Routes>
          <Route path="/" element={<QueryPage />} />
          <Route path="/upload" element={<UploadPage />} />
        </Routes>
      </main>
    </div>
  );
}
