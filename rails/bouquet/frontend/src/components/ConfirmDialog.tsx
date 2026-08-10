import { useEffect } from "react";

// A styled "Are you sure?" modal, reused for destructive confirmations (delete a
// description / analysis). Escape or the backdrop cancels; the confirm button can
// be marked danger. Renders into the module document, so it inherits the theme.
export function ConfirmDialog({
  title, body, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false,
  onConfirm, onCancel,
}: {
  title: string;
  body?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="bq-modal-backdrop" onClick={onCancel}>
      <div className="bq-confirm" role="alertdialog" aria-modal="true" aria-label={title}
        onClick={(e) => e.stopPropagation()}>
        <div className="bq-confirm-title">{title}</div>
        {body && <p className="bq-confirm-body">{body}</p>}
        <div className="bq-confirm-actions">
          <button className="bq-btn" onClick={onCancel}>{cancelLabel}</button>
          <button className={"bq-btn " + (danger ? "bq-btn-danger" : "bq-btn-primary")}
            onClick={onConfirm} autoFocus>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
