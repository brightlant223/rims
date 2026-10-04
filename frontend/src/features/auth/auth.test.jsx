import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { routes } from '../../app/routes.jsx'
import { AuthProvider } from './AuthProvider.jsx'
import { clearInMemoryCsrfToken } from '../../api/client.js'

/**
 * Frontend auth tests (FR-A1 … FR-A5).
 *
 * The fetch mock is the whole test surface: AuthProvider only ever learns the
 * session state from GET /auth/me, and the guard only ever reacts to it. Mocking
 * here means these tests assert the same contract the real server implements,
 * without booting Flask for every case.
 */

// Relative, matching the shipped default in client.js. The tests must exercise the
// same base URL the browser uses, or a same-origin regression is invisible here.
const API = '/api/v1'
const USER = { id: 1, name: 'Ruchita Admin', email: 'admin@ruchitainteriors.in', role: 'owner' }

/** Builds a Response-like object. jsdom's Response exists, but this keeps assertions simple. */
function jsonResponse(body, { status = 200, ok = true } = {}) {
  return {
    ok,
    status,
    headers: { get: () => 'application/json' },
    json: async () => body,
    text: async () => JSON.stringify(body),
  }
}

const UNAUTHORIZED = jsonResponse(
  { error: { code: 'UNAUTHENTICATED', message: 'Please sign in to continue.' } },
  { ok: false, status: 401 },
)

/**
 * A gateway response: an error status with no §9.1 envelope and a body that is
 * not JSON, which is what the Vite dev proxy returns when Flask is down.
 * `json()` rejects the way the browser's does, so the client's own `catch` is
 * exercised rather than bypassed.
 */
function gatewayError(status) {
  return {
    ok: false,
    status,
    headers: { get: () => null },
    json: async () => {
      throw new SyntaxError('Unexpected end of JSON input')
    },
    text: async () => '',
  }
}

/**
 * @param {{ me?: object, login?: object }} [responses]
 */
function mockFetch({ me = UNAUTHORIZED, login } = {}) {
  const calls = []
  const fetchMock = vi.fn(async (url, init = {}) => {
    const path = String(url).replace(API, '')
    const method = init.method || 'GET'
    calls.push({ path, method, init })

    if (path === '/auth/me' && method === 'GET') return me
    if (path === '/auth/login' && method === 'POST') {
      return login || jsonResponse({ user: USER })
    }
    if (path === '/auth/csrf' && method === 'GET') return jsonResponse({ csrf_token: 'test-token' })
    if (path === '/auth/logout' && method === 'POST') return jsonResponse({ ok: true })
    return jsonResponse({ error: { code: 'NOT_FOUND', message: 'nope' } }, { ok: false, status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

function renderAt(path, { user = undefined } = {}) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return {
    router,
    ...render(
      <AuthProvider initialUser={user}>
        <RouterProvider router={router} />
      </AuthProvider>,
    ),
  }
}

beforeEach(() => {
  // document.cookie is the CSRF source of truth in the client; clear it between
  // cases so one test's token cannot satisfy the next.
  document.cookie.split(';').forEach((entry) => {
    const name = entry.split('=')[0].trim()
    if (name) document.cookie = `${name}=; Max-Age=0; path=/`
  })
  // Also clear the in-memory CSRF token used for cross-origin scenarios.
  clearInMemoryCsrfToken()
})

describe('route protection', () => {
  it('redirects an anonymous visitor from a protected page to the login screen', async () => {
    mockFetch()
    const { router } = renderAt('/quotations')

    expect(await screen.findByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
  })

  it('lets a restored session through without showing the login screen', async () => {
    mockFetch({ me: jsonResponse({ user: USER }) })
    renderAt('/quotations')

    expect(await screen.findByRole('heading', { level: 1, name: 'Quotations' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: 'Sign in' })).not.toBeInTheDocument()
  })

  it('remembers where an anonymous visitor was headed and returns them there', async () => {
    mockFetch()
    const { router } = renderAt('/invoices/7')

    await screen.findByRole('heading', { level: 1, name: 'Sign in' })
    // RequireAuth stored the attempted location in router state.
    expect(router.state.location.state?.from?.pathname).toBe('/invoices/7')
  })

  it('sends a signed-in user away from the login screen', async () => {
    mockFetch({ me: jsonResponse({ user: USER }) })
    const { router } = renderAt('/login')

    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(screen.queryByRole('heading', { level: 1, name: 'Sign in' })).not.toBeInTheDocument()
  })

  it('requests a CSRF token before the first session check', async () => {
    const { calls } = mockFetch()
    renderAt('/')

    await waitFor(() => expect(calls.some((call) => call.path === '/auth/csrf')).toBe(true))
  })
})

describe('sign in', () => {
  it('signs in, then lands on the dashboard', async () => {
    const { calls } = mockFetch()
    const { router } = renderAt('/login')

    await userEvent.type(await screen.findByLabelText('Email'), USER.email)
    await userEvent.type(screen.getByLabelText('Password'), 'admin12345')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/'))

    const loginCall = calls.find((call) => call.path === '/auth/login')
    expect(loginCall.method).toBe('POST')
    expect(JSON.parse(loginCall.init.body)).toEqual({ email: USER.email, password: 'admin12345' })
  })

  it('sends the CSRF header on the login POST when a cookie is present', async () => {
    document.cookie = 'csrf_token=abc123; path=/'
    const { calls } = mockFetch()
    renderAt('/login')

    await userEvent.type(await screen.findByLabelText('Email'), USER.email)
    await userEvent.type(screen.getByLabelText('Password'), 'admin12345')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(calls.some((call) => call.path === '/auth/login')).toBe(true))
    const loginCall = calls.find((call) => call.path === '/auth/login')
    expect(loginCall.init.headers['X-CSRF-Token']).toBe('abc123')
  })

  it('shows the server message on bad credentials and does not reveal which field was wrong', async () => {
    mockFetch({
      me: UNAUTHORIZED,
      login: jsonResponse(
        { error: { code: 'UNAUTHENTICATED', message: 'Email or password is incorrect.' } },
        { ok: false, status: 401 },
      ),
    })
    renderAt('/login')

    await userEvent.type(await screen.findByLabelText('Email'), USER.email)
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Email or password is incorrect.')
    // Still on the login screen, and the form is usable again.
    expect(screen.getByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('toggles password visibility without losing the typed value', async () => {
    mockFetch()
    renderAt('/login')

    const passwordInput = await screen.findByLabelText('Password')
    await userEvent.type(passwordInput, 'secret123')

    const reveal = screen.getByRole('button', { name: 'Show password' })
    expect(passwordInput).toHaveAttribute('type', 'password')

    await userEvent.click(reveal)
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text')
    expect(screen.getByLabelText('Password')).toHaveValue('secret123')
  })

  it('says the server is unreachable when the request fails at the transport level', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (String(url).endsWith('/auth/me')) return UNAUTHORIZED
        if (String(url).endsWith('/auth/login')) throw new TypeError('Failed to fetch')
        return jsonResponse({})
      }),
    )
    renderAt('/login')

    await userEvent.type(await screen.findByLabelText('Email'), USER.email)
    await userEvent.type(screen.getByLabelText('Password'), 'admin12345')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText(/Cannot reach the server/i)).toBeInTheDocument()
  })

  /**
   * The reported bug, and the reason the transport-level test above was not
   * enough.
   *
   * When the Flask API is not running, the Vite dev proxy answers with a 502 that
   * has *no body*. `fetch` resolves for it — an HTTP error status is not a
   * network failure — so the `offline` flag was never set and the screen fell
   * through to its generic fallback and said "Something went wrong", which points
   * at the user rather than at a stopped server.
   *
   * These two assertions are the regression: the connectivity copy appears, and
   * the misleading generic copy does not.
   */
  it('says the server is unreachable when the dev proxy answers 502 with no body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (String(url).endsWith('/auth/me')) return UNAUTHORIZED
        if (String(url).endsWith('/auth/login')) return gatewayError(502)
        return jsonResponse({})
      }),
    )
    renderAt('/login')

    await userEvent.type(await screen.findByLabelText('Email'), USER.email)
    await userEvent.type(screen.getByLabelText('Password'), 'admin12345')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText(/Cannot reach the server/i)).toBeInTheDocument()
    expect(screen.queryByText(/Something went wrong/i)).not.toBeInTheDocument()
  })

  it.each([502, 503, 504])('treats a bodiless %i the same way on the login screen', async (status) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (String(url).endsWith('/auth/me')) return UNAUTHORIZED
        if (String(url).endsWith('/auth/login')) return gatewayError(status)
        return jsonResponse({})
      }),
    )
    renderAt('/login')

    await userEvent.type(await screen.findByLabelText('Email'), USER.email)
    await userEvent.type(screen.getByLabelText('Password'), 'admin12345')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText(/Cannot reach the server/i)).toBeInTheDocument()
  })

  it('still blames the credentials for a real 401, not the connection', async () => {
    // The counterpart to the case above: a genuine server answer must keep its own
    // copy. If a 401 were also classified as a connectivity failure, a user with a
    // wrong password would be told to check their network, which is a worse lie.
    mockFetch({
      me: UNAUTHORIZED,
      login: jsonResponse(
        { error: { code: 'UNAUTHENTICATED', message: 'Email or password is incorrect.' } },
        { ok: false, status: 401 },
      ),
    })
    renderAt('/login')

    await userEvent.type(await screen.findByLabelText('Email'), USER.email)
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Email or password is incorrect.')).toBeInTheDocument()
    expect(screen.queryByText(/Cannot reach the server/i)).not.toBeInTheDocument()
  })
})

describe('sign out', () => {
  it('returns to the login screen and clears the local session', async () => {
    mockFetch({ me: jsonResponse({ user: USER }) })
    const { router } = renderAt('/')

    await screen.findByRole('heading', { level: 1, name: 'Dashboard' })
    // `hidden: true` because jsdom does not evaluate media queries, so the sidebar
    // stays `display: none` and is out of the accessibility tree (§18.6).
    await userEvent.click(screen.getByRole('button', { name: 'Sign out', hidden: true }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
  })

  it('drops the local session even when the logout call fails', async () => {
    const { fetchMock } = mockFetch({ me: jsonResponse({ user: USER }) })
    renderAt('/')
    await screen.findByRole('heading', { level: 1, name: 'Dashboard' })

    fetchMock.mockImplementation(async (url) => {
      const path = String(url).replace(API, '')
      if (path === '/auth/logout') throw new TypeError('Failed to fetch')
      if (path === '/auth/me') return jsonResponse({ user: USER })
      return jsonResponse({})
    })

    await userEvent.click(screen.getByRole('button', { name: 'Sign out', hidden: true }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Sign in' })).toBeInTheDocument()
  })
})

describe('login validation & branding (Phase 3 polish)', () => {
  it('blocks an empty submit with inline messages and never calls the API', async () => {
    const { calls } = mockFetch()
    renderAt('/login')

    await userEvent.click(await screen.findByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Enter your email address.')).toBeInTheDocument()
    expect(screen.getByText('Enter your password.')).toBeInTheDocument()
    expect(calls.some((call) => call.path === '/auth/login')).toBe(false)
  })

  it('rejects a malformed email before any request', async () => {
    const { calls } = mockFetch()
    renderAt('/login')

    await userEvent.type(await screen.findByLabelText('Email'), 'not-an-email')
    await userEvent.type(screen.getByLabelText('Password'), 'whatever-123')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument()
    expect(calls.some((call) => call.path === '/auth/login')).toBe(false)
  })

  it('renders the optimized SVG logo and no legacy wordmark asset', async () => {
    mockFetch()
    renderAt('/login')
    await screen.findByRole('heading', { level: 1, name: 'Sign in' })

    const svgLogos = Array.from(document.querySelectorAll('img')).filter(
      (img) => img.getAttribute('src') === '/brand/logo.svg',
    )
    expect(svgLogos.length).toBeGreaterThan(0)
    // Every logo instance keeps its intrinsic ratio hint (no distortion).
    for (const logo of svgLogos) {
      expect(logo).toHaveAttribute('width')
      expect(logo).toHaveAttribute('height')
    }
    expect(document.querySelector('img[src*="wordmark"]')).toBeNull()
  })

  it('carries the desktop brand panel copy beside the form', async () => {
    mockFetch()
    renderAt('/login')
    await screen.findByRole('heading', { level: 1, name: 'Sign in' })

    expect(screen.getByText(/one calm place to run the studio/)).toBeInTheDocument()
  })
})
