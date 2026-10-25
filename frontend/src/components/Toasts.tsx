import { useStore } from "../store";

export function Toasts() {
  const { toasts, dismissToast, askOpen } = useStore();
  return (
    <div className={`toasts${askOpen ? " beside-ask" : ""}`} role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tone ?? ""}`} onClick={() => dismissToast(t.id)}>
          {t.text}
        </div>
      ))}
    </div>
  );
}
