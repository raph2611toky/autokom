import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Info, X } from 'lucide-react'

/** Accessible, theme-aware alternative to native confirm/prompt. Never resolves an action implicitly. */
export default function ActionDialog({ open, title, description, children, confirmLabel = 'Confirmer',
  cancelLabel = 'Annuler', variant = 'warning', requiredText = '', busy = false, onConfirm, onClose }) {
  const titleId = useId()
  const descId = useId()
  const panel = useRef(null)
  const [typed, setTyped] = useState('')
  const closeCallback = useRef(onClose)
  const blocking = useRef(busy)
  closeCallback.current = onClose
  blocking.current = busy
  useEffect(() => {
    if (!open) return undefined
    setTyped('')
    const previous = document.activeElement
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const target = panel.current?.querySelector('[data-dialog-initial-focus]')
    target?.focus()
    const onKeyDown = event => {
      if (event.key === 'Escape' && !blocking.current) { event.preventDefault(); closeCallback.current?.(); return }
      if (event.key !== 'Tab' || !panel.current) return
      const controls = [...panel.current.querySelectorAll('button:not(:disabled), input:not(:disabled)')]
      if (!controls.length) return
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      previous?.focus?.()
    }
  }, [open])
  if (!open) return null
  const confirmed = !requiredText || typed === requiredText
  return createPortal(
    <div className="ak-dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose?.() }}>
      <section className={`ak-dialog ak-dialog--${variant}`} ref={panel} role="dialog" aria-modal="true"
        aria-labelledby={titleId} aria-describedby={descId}>
        <div className="ak-dialog-header">
          <span className="ak-dialog-symbol" aria-hidden="true">{variant === 'danger' || variant === 'warning' ? <AlertTriangle size={22}/> : <Info size={22}/>}</span>
          <h2 id={titleId}>{title}</h2>
          <button className="ak-dialog-close" type="button" aria-label="Fermer" disabled={busy} onClick={onClose}><X size={19}/></button>
        </div>
        <div className="ak-dialog-content" id={descId}>
          {description && <p>{description}</p>}
          {children}
          {requiredText && <label className="ak-dialog-confirm-input">Saisissez exactement <strong>{requiredText}</strong> pour confirmer
            <input value={typed} onChange={event => setTyped(event.target.value)} autoComplete="off"
              spellCheck={false} aria-label="Confirmation du nom du projet" data-dialog-initial-focus />
          </label>}
        </div>
        <div className="ak-dialog-actions">
          <button className="button-outline" type="button" data-dialog-initial-focus={!requiredText ? '' : undefined} disabled={busy} onClick={onClose}>{cancelLabel}</button>
          <button className={variant === 'danger' ? 'ak-dialog-danger-button' : 'button-primary'} type="button"
            disabled={busy || !confirmed} onClick={() => onConfirm?.()}>{busy ? 'En cours…' : confirmLabel}</button>
        </div>
      </section>
    </div>, document.body)
}
