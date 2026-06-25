import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { AuthProvider } from './auth';

vi.mock('./pages/QueryPage', () => ({
  default: () => <div>Query workspace</div>,
}));

vi.mock('./pages/UploadPage', () => ({
  default: () => <div>Upload workspace</div>,
}));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderApp(path = '/') {
  return render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
    >
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('authenticated application shell', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a no-signup login when startup authentication fails', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ detail: { error: 'authentication_required' } }, 401),
    );

    renderApp();

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByText(/sign up/i)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/me', expect.objectContaining({
      credentials: 'include',
    }));
  });

  it('logs in and shows the authenticated identity', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ detail: { error: 'authentication_required' } }, 401))
      .mockResolvedValueOnce(jsonResponse({
        username: 'igna.user@gmail.com',
        role: 'user',
      }));
    const user = userEvent.setup();

    renderApp();
    await user.type(await screen.findByLabelText('Email'), 'igna.user@gmail.com');
    await user.type(screen.getByLabelText('Password'), 'user@123');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByText('igna.user@gmail.com')).toBeInTheDocument();
    expect(screen.getByText('user')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith('/api/auth/login', expect.objectContaining({
      credentials: 'include',
    }));
  });

  it('redirects a user away from upload and hides admin controls', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({
      username: 'igna.user@gmail.com',
      role: 'user',
    }));

    renderApp('/upload');

    expect(await screen.findByText('Query workspace')).toBeInTheDocument();
    expect(screen.queryByText('Upload CSV')).not.toBeInTheDocument();
  });

  it('lets a super admin open upload', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({
      username: 'igna.admin@gmail.com',
      role: 'super_admin',
    }));

    renderApp('/upload');

    expect(await screen.findByText('Upload workspace')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Upload CSV' })).toBeInTheDocument();
    expect(screen.getByText('super admin')).toBeInTheDocument();
  });

  it('logs out to the login screen', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({
        username: 'igna.user@gmail.com',
        role: 'user',
      }))
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }));
    const user = userEvent.setup();

    renderApp();
    await user.click(await screen.findByRole('button', { name: 'Log out' }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenLastCalledWith('/api/auth/logout', expect.objectContaining({
      credentials: 'include',
    }));
  });
});
