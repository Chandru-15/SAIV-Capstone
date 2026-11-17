'use client';

import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

/** Stable dialog shell: changing instructions never remounts its capture children. */
export default function ModalFrame({ children, label, labelledBy, onClose, busy = false, panelClassName = '' }: {
  children: ReactNode; label?: string; labelledBy?: string; onClose: () => void; busy?: boolean; panelClassName?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const actions = useRef({ onClose, busy });
  actions.current = { onClose, busy };
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog || typeof document === 'undefined') return;
    const previous = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!actions.current.busy) actions.current.onClose();
      }
      if (event.key !== 'Tab') return;
      const items = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]'))
        .filter(item => item.getClientRects().length > 0);
      const first = items[0], last = items[items.length - 1];
      if (!first) { event.preventDefault(); dialog.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) { event.preventDefault(); first.focus(); }
    };
    dialog.addEventListener('keydown', keydown);
    return () => {
      dialog.removeEventListener('keydown', keydown);
      document.body.style.overflow = overflow;
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  return <div ref={ref} role="dialog" aria-modal="true" aria-label={label} aria-labelledby={labelledBy} tabIndex={-1} className="modal-backdrop">
    <div className={`modal-panel max-w-xl ${panelClassName}`}>{children}</div>
  </div>;
}
