/**
 * Ruchita Interiors — settings API calls (§9.2).
 *
 * Thin wrappers over `client.js`. `client.js` hands back the raw §9.1 envelope,
 * so each function here unwraps `data` — callers only ever see the payload.
 */

import { BASE_URL, del, get, post, put } from '../client.js'

/** GET /settings/company → the singleton settings row. */
export const fetchCompanySettings = () => get('/settings/company').then((body) => body.data)

/** PUT /settings/company → save the editable fields; returns the normalized row. */
export const saveCompanySettings = (patch) => put('/settings/company', patch).then((body) => body.data)

/** GET /settings/terms → all terms entries. */
export const fetchTerms = () => get('/settings/terms').then((body) => body.data)

/** POST /settings/terms → create one. */
export const createTerm = (term) => post('/settings/terms', term).then((body) => body.data)

/** PUT /settings/terms/:id → update one. */
export const updateTerm = (id, patch) => put(`/settings/terms/${id}`, patch).then((body) => body.data)

/** DELETE /settings/terms/:id → remove one. */
export const deleteTerm = (id) => del(`/settings/terms/${id}`).then((body) => body.data)

/** POST /settings/logo (multipart) → upload a new logo. */
export const uploadLogo = (file) => {
  const form = new FormData()
  form.append('logo', file)
  return post('/settings/logo', form).then((body) => body.data)
}

/** DELETE /settings/logo → remove the stored logo. */
export const removeLogo = () => del('/settings/logo').then((body) => body.data)

/** POST /settings/payment-qr (multipart) → upload a new payment QR. */
export const uploadPaymentQr = (file) => {
  const form = new FormData()
  form.append('payment_qr', file)
  return post('/settings/payment-qr', form).then((body) => body.data)
}

/** DELETE /settings/payment-qr → remove the stored payment QR. */
export const removePaymentQr = () => del('/settings/payment-qr').then((body) => body.data)

/**
 * URL of the stored logo, or null when none is set.
 *
 * The image is served by the authenticated `GET /uploads/logo` route, so an
 * `<img>` src works: the cookie travels with same-origin image requests. The
 * `updated_at` query busts the long-lived cache after a re-upload.
 */
export const logoImageUrl = (settings) => {
  if (!settings?.logo_path) return null
  const version = settings.updated_at || ''
  // BASE_URL, not import.meta.env.VITE_API_BASE_URL: it is the value that
  // client.js has already normalized with the /api/v1 prefix. Reading the raw
  // env var here was the surviving half of audit finding C1 -- an env value
  // missing the prefix (e.g. https://rqis.pythonanywhere.com) made this return
  // /uploads/logo, which the Flask SPA fallback answers with index.html, so the
  // logo would render as a broken image while every other API call worked.
  return `${BASE_URL}/uploads/logo?v=${encodeURIComponent(version)}`
}

/**
 * URL of the live payment QR, or null when none is set (§8.5).
 *
 * Deliberately resolved from the *live* settings row on every render rather
 * than from `Invoice.bank_snapshot`. The bank text is snapshotted because it
 * states the terms an invoice was issued under; a QR is a payment *instruction*
 * that can change (new account, closed UPI handle). A snapshot would keep
 * printing a QR that sends money to the wrong place, so the QR is read live and
 * an invoice is the only document that shows one — a quotation is not payable.
 */
export const paymentQrImageUrl = (settings) => {
  if (!settings?.payment_qr_path) return null
  const version = settings.updated_at || ''
  // See logoImageUrl above: BASE_URL is the normalized base, not the raw env var.
  return `${BASE_URL}/uploads/payment-qr?v=${encodeURIComponent(version)}`
}
