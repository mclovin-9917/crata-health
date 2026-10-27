import { AlertTriangle } from "lucide-react";

/** Warning signs found by the server's code: the red card leads, before anything else. */
export function EmergencyNow({ signs, helpline }: { signs: string[]; helpline?: string | null }) {
  return (
    <section className="rounded-card bg-red-700 p-5 text-white shadow-lg print:hidden" role="alert" aria-label="Emergency">
      <div className="flex items-start gap-3">
        <AlertTriangle size={28} className="shrink-0" aria-hidden />
        <div className="min-w-0 space-y-2">
          <h2 className="text-xl font-bold tracking-tight">Get emergency care now</h2>
          {signs.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5">
              {signs.map((s) => <li key={s}>{s}</li>)}
            </ul>
          )}
          <p className="font-semibold">Call 112 or go to the nearest emergency department.</p>
          {helpline && <p className="font-semibold">{helpline}</p>}
        </div>
      </div>
    </section>
  );
}
