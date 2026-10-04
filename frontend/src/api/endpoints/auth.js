/**
 * Ruchita Interiors — auth API calls.
 *
 * Thin wrappers over `client.js`. Every function returns the unwrapped `data`
 * payload, because that is the only shape callers should deal with (§9.1).
 */

import { apiRequest, del, ensureCsrfToken, get, post, put, clearInMemoryCsrfToken } from '../client.js'

/** Ask for a CSRF token. Called once on app start, before the first mutation. */
export const bootstrapCsrf = () => ensureCsrfToken()

/** GET /auth/csrf — issue the double-submit cookie. */
export const fetchCsrfToken = () => get('/auth/csrf')

/** POST /auth/login */
export const login = (email, password) => post('/auth/login', { email, password })

/** POST /auth/logout */
export const logout = async () => {
  const result = await post('/auth/logout')
  clearInMemoryCsrfToken()
  return result
}

/** POST /auth/refresh */
export const refreshSession = () => post('/auth/refresh')

/** GET /auth/me */
export const fetchCurrentUser = () => get('/auth/me')

/** PUT /auth/password */
export const changePassword = (currentPassword, newPassword) =>
  put('/auth/password', { current_password: currentPassword, new_password: newPassword })

export { apiRequest, del }
