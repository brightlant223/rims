/**
 * Ruchita Interiors — branded document sheet (§14.3, FR-DOC1, FR-DOC2).
 *
 * A presentational A4 sheet for a quotation. It fetches nothing and holds no
 * state: the caller passes the server's document payload plus the company
 * settings row, so what renders is always the authoritative data rather than a
 * client-side copy (§10.1, D2).
 *
 * Layout order is the §14.3 spec, in order: header band, title strip, client
 * block, items table, totals block, terms, signatory, footer. The invoice
 * variant's extra blocks (amount paid, balance due, bank details) are Phase 7 —
 * this component renders the quotation family only.
 *
 * Multi-page behaviour (§14.4) lives in CSS, not JS: the `<thead>` repeats on
 * every page (`display: table-header-group`), rows refuse to break across pages,
 * and the totals block is never orphaned. Page numbers are deliberately omitted
 * rather than faked (FR-DOC4).
 *
 * `titleLevel` exists for one reason: the print route needs a single `<h1>`, and
 * the preview overlay must drop to `<h2>` so the editor's page heading stays the
 * only level-one heading on screen.
 *
 * @param {{
 *   document: object,
 *   settings?: object,
 *   logoSrc?: string | null,
 *   titleLevel?: 1 | 2,
 *   numberOverride?: string,
 *   isUnsaved?: boolean,
 * }} props
 */

import { calcLineTotal, milliToInput } from '../../lib/calc.js'
import { formatDate } from '../../lib/format.js'
import { formatPaise } from '../../lib/money.js'
import { formatPhone } from '../../lib/phone.js'
import PaymentDetailsCard from '../payments/PaymentDetailsCard.jsx'
import DocumentHeader from './DocumentHeader.jsx'
import Icon from '../../components/ui/Icon.jsx'
import styles from './DocumentPaper.module.css'

/**
 * Whether this document is payable.
 *
 * A quotation is not, so it never shows a UPI QR — a payment instruction on a
 * document that is not yet a bill is the kind of thing that gets paid by mistake.
 */
function isInvoiceDocKind(docKind) {
  return docKind === 'invoice'
}

/**
 * FR-P8: the invoice's payment *presentation*, and how each choice reads on paper.
 *
 * `doc.payment_method` is the admin's choice of which instructions to print, made
 * while the invoice was a Draft and frozen at issue by the server's draft-only edit
 * guard. It is NOT `latest_payment_method`, which the server derives from the
 * payment ledger and which must never influence a printed document: an invoice
 * issued as UPI still prints UPI after the client pays by bank transfer.
 *
 * The four states, and what each is allowed to show:
 *   upi            -> UPI id, payee, QR, "Scan to Pay". Never bank details.
 *   bank_transfer  -> account holder, bank, account number, IFSC. Never a QR or VPA.
 *   cash           -> the method line alone. No rails at all.
 *   null           -> both electronic rails, so the client can pay either way. Cash is
 *                     never presented as an instruction here; it is a choice the admin
 *                     makes deliberately, not a default.
 */
const PAYMENT_PRESENTATION = {
  upi: { label: 'UPI', showUpi: true, showBank: false },
  bank_transfer: { label: 'Bank transfer', showUpi: false, showBank: true },
  cash: { label: 'Cash', showUpi: false, showBank: false },
  default: { label: 'UPI + Bank transfer', showUpi: true, showBank: true },
}

function paymentPresentation(method) {
  return PAYMENT_PRESENTATION[method] || PAYMENT_PRESENTATION.default
}

/** Join the non-empty parts of an address into a single printable line. */
function joinAddress(...parts) {
  return parts
    .map((part) => (part == null ? '' : String(part).trim()))
    .filter(Boolean)
    .join(', ')
}

export function groupItemsByCategory(items) {
  const groups = []
  let index = 0
  for (const item of items || []) {
    const category = (item.category || '').trim()
    const last = groups[groups.length - 1]
    if (category && last && last.category === category) {
      last.items.push(item)
    } else {
      groups.push({ category, items: [item], startIndex: index })
    }
    index += 1
  }
  return groups
}

export default function DocumentPaper({
  document: doc,
  settings = null,
  logoSrc = null,
  titleLevel: TitleLevel = 'h1',
  numberOverride = null,
  isUnsaved = false,
  docKind = 'quotation',
}) {
  // The logo / wordmark fallback lives in `DocumentHeader` now, so this component holds
  // no state at all: it is a pure function of the document payload.

  // Phase 7: one sheet, two document kinds. The shared spine — header, client
  // block, items, tax breakdown, terms, signatory, footer — is identical; only
  // the title, the date pair and the money rows below the grand total differ.
  const isInvoice = isInvoiceDocKind(docKind)
  const title = isInvoice ? 'Tax Invoice' : 'Quotation'
  const documentLabel = isInvoice ? 'Invoice document' : 'Quotation document'

  const s = settings || {}
  const number = numberOverride || doc.number || ''

  // The payment QR is now **generated** from a UPI intent URI rather than fetched
  // as an uploaded image (Phase 8 shipped the static version; see PLAN.md FR-P6,
  // amended). Two consequences worth stating on the sheet itself:
  //
  // 1. It needs a UPI ID, not an image. With none configured there is nothing to
  //    encode, so the subsection is dropped and the UPI ID in the bank rows above
  //    remains the way to pay — an omitted QR reads as a gap, a broken image reads
  //    as a defect.
  // 2. The amount is the *outstanding* balance, read live from the ledger. That is
  //    the behaviour Phase 8's static image could not have: a customer's own printed
  //    copy of a partially-paid invoice can no longer over-collect. The cost, stated
  //    plainly below the QR, is that paper is a snapshot — if the balance moves after
  //    printing, the printed amount is stale. Hence "Verify the amount before
  //    paying" rather than silence.
  // The live UPI ID, from Settings and never from `bank_snapshot` (the 8.4 exception
  // Phase 8 already made): a QR or an address is an instruction to send money
  // somewhere, not a term of the invoice, and a frozen one would keep directing
  // customers at an account the owner has since closed.
  const liveUpiId = (s.upi_id || '').trim()
  const client = doc.client_snapshot || {}
  const items = doc.items || []

  const subtotal = doc.subtotal_paise || 0
  const discount = doc.discount_paise || 0
  const gst = doc.gst_paise || 0
  const otherCharges = doc.other_charges_paise || 0
  const grandTotal = doc.grand_total_paise || 0
  const taxable = subtotal - discount
  const isPercentDiscount = (doc.discount_type || 'percent') === 'percent'
  const discountLabel = isPercentDiscount ? `Discount (${(doc.discount_bp || 0) / 100}%)` : 'Discount'
  const gstLabel = `GST ${(doc.gst_bp || 0) / 100}%`

  // `paid_paise`, `outstanding_paise` and `payment_status` are **deliberately not read
  // here**, though the server sends all three on this payload. A tax invoice is a fixed
  // legal document of what was billed: it must print identically before any payment and
  // after the last one, so anything derived from the ledger would make a reprint a
  // different document from the one the customer already holds. Those figures live on the
  // application UI and on the Balance / Payment Due sheet, which is derived from the
  // ledger on every request (§8.5). Nothing below this line may reintroduce them.

  const clientAddress = joinAddress(client.address, client.city, client.state, client.pincode)

  const bank = doc.bank_snapshot || null
  const signatory = doc.signatory_name || s.signatory_name || 'Authorised Signatory'
  // Deliberately omits `upi_id`, which the snapshot also carries. The UPI address is
  // read live and printed once in the hint below: printing the frozen one here as
  // well would print two different addresses whenever the owner has since changed
  // theirs, and the customer would have no way to tell which to pay.
  const bankLines = bank
    ? [
        bank.account_name && { label: 'Account name', value: bank.account_name },
        bank.account_number && { label: 'Account number', value: bank.account_number },
        bank.bank_name && { label: 'Bank', value: bank.bank_name },
        bank.branch && { label: 'Branch', value: bank.branch },
        bank.ifsc && { label: 'IFSC', value: bank.ifsc },
      ].filter(Boolean)
    : []

  // FR-P8: the presentation matrix, applied to the invoice's stored choice. `cash`
  // is the only state with no rail, so it prints the method line on its own; the
  // `showQr` guard is what keeps a QR from appearing on a bank-transfer or cash
  // invoice even though a UPI ID exists in Settings.
  const presentation = paymentPresentation(isInvoice ? doc.payment_method : null)
  const showUpi = isInvoice && presentation.showUpi && Boolean(liveUpiId)
  const showBank = isInvoice && presentation.showBank
  // The invoice's own QR encodes the GRAND TOTAL, never the outstanding balance.
  // An outstanding amount is not knowable when the document is issued and moves
  // with every payment, so encoding it would mean a reprint of the same invoice
  // asked for a different sum - which is what §8.4 forbids of a tax document. The
  // Balance / Payment Due document exists precisely to collect a reduced sum, and
  // it is regenerated per collection.
  const showQr = showUpi
  // Only worth naming a method when the choice actually changes what is printed.
  // "UPI + Bank transfer" is a self-describing block of two rail sections; a Cash
  // invoice's only instruction is the word itself.
  const showMethod = isInvoice && !presentation.showUpi && !presentation.showBank
  const payeeName = (s.company_name || '').trim()
  const bankRows = showBank ? bankLines : []

  // Whether the card will have anything at all to print. Mirrors the card's own guard,
  // and is deliberately *not* just `showBank`: a bank-transfer invoice with an empty
  // snapshot would otherwise mount a section around a card that renders nothing, which
  // is a bare top border on the customer's page.
  const hasPaymentBlock =
    showMethod || showQr || (showBank && bankRows.length > 0) || (showUpi && Boolean(liveUpiId))

  return (
    <article className={styles.paper} data-document-paper aria-label={documentLabel} data-theme="light">
      {/* 1. Header band — the same component the Balance / Payment Due sheet uses, so
          the two documents cannot present different letterheads (see DocumentHeader). */}
      <DocumentHeader settings={s} logoSrc={logoSrc} />

      {/* 2. Title strip */}
      <div className={styles.titleStrip}>
        <div className={styles.dates}>
          <p className={styles.dateLine}>
            <Icon name="calendar" size={15} strokeWidth={1.5} className={styles.labelIcon} />
            <span className={styles.microLabel}>{isInvoice ? 'Issue date' : 'Date'}</span>
            <span className={styles.dateValue}>
              {formatDate(isInvoice ? doc.issue_date : doc.quotation_date) || '—'}
            </span>
          </p>
          <p className={styles.dateLine}>
            <Icon name="calendar" size={15} strokeWidth={1.5} className={styles.labelIcon} />
            <span className={styles.microLabel}>{isInvoice ? 'Due date' : 'Valid until'}</span>
            <span className={styles.dateValue}>
              {formatDate(isInvoice ? doc.due_date : doc.valid_until) || '—'}
            </span>
          </p>
        </div>
        <div className={styles.heading}>
          <TitleLevel className={styles.docTitle}>{title}</TitleLevel>
          <p className={styles.docNumber}>{isUnsaved ? 'Draft — not yet saved' : number || 'Draft'}</p>
        </div>
      </div>

      {/* 3. Client block */}
      <section className={styles.clientBlock}>
        <div className={styles.clientCell}>
          <h2 className={styles.blockLabel}>
            <Icon name="user" size={15} strokeWidth={1.5} className={styles.labelIcon} />
            <span>Bill To</span>
          </h2>
          <p className={styles.clientName}>{client.name || '—'}</p>
          {clientAddress ? (
            <p className={styles.clientLine}>
              <Icon name="mapPin" size={14} strokeWidth={1.5} className={styles.inlineIcon} />
              <span>{clientAddress}</span>
            </p>
          ) : null}
          {client.phone ? (
            <p className={styles.clientLine}>
              <Icon name="phone" size={14} strokeWidth={1.5} className={styles.inlineIcon} />
              <span>{formatPhone(client.phone)}</span>
            </p>
          ) : null}
          {client.email ? (
            <p className={styles.clientLine}>
              <Icon name="mail" size={14} strokeWidth={1.5} className={styles.inlineIcon} />
              <span>{client.email}</span>
            </p>
          ) : null}
          {client.gstin ? (
            <p className={styles.clientLine}>
              <Icon name="badgeCheck" size={14} strokeWidth={1.5} className={styles.inlineIcon} />
              <span>GSTIN: {client.gstin}</span>
            </p>
          ) : null}
        </div>
        <div className={styles.clientCell}>
          <h2 className={styles.blockLabel}>
            <Icon name="mapPin" size={15} strokeWidth={1.5} className={styles.labelIcon} />
            <span>Project / Site Address</span>
          </h2>
          {client.project_address ? (
            <p className={styles.clientLine}>
              <Icon name="mapPin" size={14} strokeWidth={1.5} className={styles.inlineIcon} />
              <span>{client.project_address}</span>
            </p>
          ) : (
            <p className={styles.clientLine}>—</p>
          )}
        </div>
      </section>

      {/* 4. Items table */}
      <table className={styles.items} data-document-items>
        <thead>
          <tr>
            <th scope="col" className={styles.colIndex}>
              #
            </th>
            <th scope="col" className={styles.colCategory}>
              Category
            </th>
            <th scope="col" className={styles.colItem}>
              Item &amp; Description
            </th>
            <th scope="col" className={styles.colUnit}>
              Unit
            </th>
            <th scope="col" className={`${styles.colNum} ${styles.center}`}>
              Qty
            </th>
            <th scope="col" className={styles.colNum}>
              Rate
            </th>
            <th scope="col" className={styles.colNum}>
              Amount
            </th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={7} className={styles.emptyRow}>
                No items yet.
              </td>
            </tr>
          ) : (
            groupItemsByCategory(items).map((group, groupIndex) => (
              <GroupRows
                key={`${group.category || 'ungrouped'}-${groupIndex}`}
                group={group}
                startIndex={group.startIndex}
              />
            ))
          )}
        </tbody>
      </table>

      {/* 5. Totals block */}
      <section className={styles.totalsBlock} data-document-totals>
        <dl className={styles.totals}>
          <div className={styles.totalRow}>
            <dt>Subtotal</dt>
            <dd>{formatPaise(subtotal)}</dd>
          </div>
          {discount > 0 ? (
            <div className={styles.totalRow}>
              <dt>{discountLabel}</dt>
              <dd>−{formatPaise(discount)}</dd>
            </div>
          ) : null}
          <div className={styles.totalRow}>
            <dt>Taxable Value</dt>
            <dd>{formatPaise(taxable)}</dd>
          </div>
          <div className={styles.totalRow}>
            <dt>{gstLabel}</dt>
            <dd>{formatPaise(gst)}</dd>
          </div>
          {otherCharges > 0 ? (
            <div className={styles.totalRow}>
              <dt>{doc.other_charges_label || 'Other Charges'}</dt>
              <dd>{formatPaise(otherCharges)}</dd>
            </div>
          ) : null}
          <div className={`${styles.totalRow} ${styles.totalGrand}`}>
            <dt>Grand Total</dt>
            <dd>{formatPaise(grandTotal)}</dd>
          </div>
          {/* Deliberately nothing below this line. An earlier version ended the invoice
              with "Amount Paid" and "Balance Due", which are both derived from the
              payment ledger — so the same fixed tax document changed the moment a
              payment was recorded, and a customer holding an already-printed copy could
              be handed a second, different invoice for the same billing. The totals
              block now stops at Grand Total, which is the whole amount billed and the
              one figure this document is a legal statement of. What is still owed is
              stated on the Balance / Payment Due sheet, which is re-derived per
              collection. */}
        </dl>
      </section>

      {/* 5b. Payment details (§8.5, FR-P8).
       *
       * `PaymentDetailsCard` is the same block the Balance / Payment Due sheet prints,
       * so bank details and the scan code are laid out identically on both and appear
       * exactly once per document.
       *
       * The QR here is **generated** from a UPI intent URI, not an uploaded image, and it
       * encodes the invoice's GRAND TOTAL — never the outstanding balance. An outstanding
       * amount is unknowable at issue and moves with every payment, so encoding it would
       * mean a reprint of the same invoice asked for a different figure, which is the
       * exact behaviour §8.4 exists to prevent and the one thing a permanent tax document
       * must never do. Collecting a reduced sum is the Balance / Payment Due document's
       * job, and that document is regenerated per collection.
       *
       * So the division of responsibility is:
       *   - this sheet: the payment *instructions*, and the grand total it states as
       *     billed. Static. Safe to reprint any time, forever.
       *   - the Balance / Payment Due document: what is still owed. Derived, never
       *     stored, regenerated per collection.
       *
       * The UPI ID is read live from Settings rather than the snapshot: a closed account
       * must not stay on issued documents. The *amount* printed and encoded is the
       * invoice's own saved grand total.
       */}
      {/* The card returns `null` when it has nothing to print, so the section wrapper is
          mounted only when the card will actually render — otherwise an empty section
          with a top border would print as a stray hairline and a stray gap on the
          customer's document (§21.24). `hasPaymentBlock` is the same question the card
          asks, computed here from the same values. */}
      {isInvoice && hasPaymentBlock ? (
        <section className={styles.payment} data-document-payment>
          <PaymentDetailsCard
            bankRows={bankRows}
            upiId={showUpi ? liveUpiId : ''}
            showBank={showBank}
            showQr={showQr}
            amountPaise={grandTotal}
            payeeName={payeeName}
            note={number}
            methodLabel={showMethod ? presentation.label : null}
            hint={
              showUpi
                ? `Please verify the amount before paying${number ? `, and quote ${number}.` : '.'}`
                : null
            }
          />
        </section>
      ) : null}

      {/* 7. Terms & Conditions */}
      {doc.terms_text ? (
        <section className={styles.terms} data-document-terms>
          <h2 className={styles.blockLabel}>
            <Icon name="scrollText" size={15} strokeWidth={1.5} className={styles.labelIcon} />
            <span>Terms &amp; Conditions</span>
          </h2>
          <TermsList text={doc.terms_text} />
        </section>
      ) : null}

      {/* 8. Signatory */}
      <section className={styles.signatory} data-document-signatory>
        <p className={styles.signatoryLabel}>For {s.company_name || 'Ruchita Interiors'}</p>
        <p className={styles.signatoryName}>{signatory}</p>
        <div className={styles.signatureLine} aria-hidden="true" />
      </section>

      {s.footer_text ? <footer className={styles.footer}>{s.footer_text}</footer> : null}
    </article>
  )
}

/**
 * One category group: an optional subheader row, then its item rows.
 *
 * The `#` column counts across the whole document rather than restarting per
 * group, so a grouped quote is still readable line by line.
 */
function GroupRows({ group, startIndex }) {
  return (
    <>
      {group.category ? (
        <tr className={styles.categoryRow}>
          <td className={styles.colIndex} />
          <td className={styles.colCategory} colSpan={6}>
            {group.category}
          </td>
        </tr>
      ) : null}
      {group.items.map((item, offset) => (
        <ItemRow key={item.id ?? `${startIndex}-${offset}`} item={item} index={startIndex + offset + 1} />
      ))}
    </>
  )
}

function ItemRow({ item, index }) {
  const lineTotal = item.line_total_paise ?? calcLineTotal(item.qty_milli, item.rate_paise)
  return (
    <tr className={styles.itemRow}>
      <td className={`${styles.colIndex} ${styles.center}`}>{index}</td>
      {/* The category is carried by the group's subheader row, not repeated on
          every line: printing it twice would be noise on the page. The cell has to
          stay — `table-layout: fixed` sizes columns from the header, so dropping it
          would slide the item text one column left — but it is marked as empty so it
          can drop its rule. A bordered blank box under every line reads as a missing
          value on a customer's document. */}
      <td className={`${styles.colCategory} ${styles.colEmpty}`} data-empty-cell="true" />
      <td className={styles.colItem}>
        <span className={styles.itemName}>{item.name}</span>
        {item.description ? <span className={styles.itemDescription}>{item.description}</span> : null}
      </td>
      <td className={`${styles.colUnit} ${styles.center}`}>{item.unit || ''}</td>
      {/* Quantity only. The unit used to be repeated here as well as in its own
          column, which contradicted the 14.3 column spec and — because this cell
          is `white-space: nowrap` — made a two-word unit like "running ft" an
          unbreakable token wide enough to widen the whole table. */}
      <td className={`${styles.colNum} ${styles.center}`}>{milliToInput(item.qty_milli) || '0'}</td>
      <td className={`${styles.colNum} ${styles.num}`}>{formatPaise(item.rate_paise)}</td>
      <td className={`${styles.colNum} ${styles.num}`}>{formatPaise(lineTotal)}</td>
    </tr>
  )
}

/**
 * A leading "1." / "2)" / "3 -" typed into the terms textarea.
 *
 * Admins paste terms from documents that already number their lines, so the raw text
 * commonly reads "1. 50% advance is required…". Rendered verbatim into an `<ol>` that
 * produces "1. 1. 50% advance is required…" on the printed document — doubled numbering
 * on a customer's tax document. The marker is stripped here and the `<ol>` supplies the
 * single numbering, so the stored text is untouched and both styles of input look the
 * same on paper.
 *
 * Anchored to the start and limited to one marker, so a term that merely *begins* with a
 * number ("50% advance on order of…") is never truncated.
 */
const LEADING_MARKER = /^\s*\d+\s*[.)-]\s+/

/**
 * Render terms text as a numbered list.
 *
 * `terms_text` is plain text, not HTML (§8.3), so it is split on newlines and
 * rendered as list items. Non-empty lines only — a blank line in a textarea
 * should not become an empty numbered term.
 */
function TermsList({ text }) {
  const lines = String(text)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.replace(LEADING_MARKER, ''))

  if (lines.length === 0) return null

  return (
    <ol className={styles.termList}>
      {lines.map((line, index) => (
        <li key={index} className={styles.termItem}>
          {line}
        </li>
      ))}
    </ol>
  )
}
