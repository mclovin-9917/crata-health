import { AlertTriangle, ChevronRight, HeartHandshake, MapPin, ShieldCheck, Siren } from "lucide-react";
import { api, type AccessLogEntry, type Alert } from "../api";
import { useMe } from "../App";
import { Disclosure, ErrorNote } from "../components";
import { accessWhat, emergencyAlert, formatDate, formatDateTime, formatTime, go, startedEmergencyEntry, useLoad } from "../lib";

/** Small facts under an access line: where, and the registry number for a doctor. */
function AccessFacts({ e }: { e: AccessLogEntry }) {
  const registry = e.actor_role === "doctor" && e.actor_registry_id;
  if (!e.place && !registry) return null;
  return (
    <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink-500">
      {e.place && (
        <span className="inline-flex items-center gap-1">
          <MapPin size={12} aria-hidden /> {e.place}
        </span>
      )}
      {registry && <span>Registry {e.actor_registry_id} · {e.verified ? "verified" : "not verified"}</span>}
    </p>
  );
}

export default function AccessScreen() {
  const me = useMe();
  const alerts = useLoad(() => api.alerts(), [me.id]);
  const log = useLoad(() => api.accessLog(me.id), [me.id]);

  const update = async (a: Alert, body: { read?: boolean; reported?: boolean }) => {
    const updated = await api.updateAlert(a.id, body);
    alerts.setData((prev) => (prev ?? []).map((x) => (x.id === a.id ? updated : x)));
  };

  return (
    <div className="space-y-4">
      <h1 className="title">Alerts and access log</h1>

      <div className="card">
        <h2 className="section-title mb-3">Alerts</h2>
        <ErrorNote error={alerts.error} />
        {alerts.data?.length === 0 && <p className="text-sm text-ink-500">No alerts.</p>}
        <ul className="space-y-2">
          {alerts.data?.map((a) => {
            // Structured only for the patient's own emergency-card openings; the rest keep the server's sentence.
            const entry =
              a.patient_id === me.id && a.log_entry_id
                ? log.data?.entries.find((e) => e.id === a.log_entry_id && e.reason === "emergency")
                : undefined;
            return (
              <li
                key={a.id}
                className={`rounded-xl p-3 text-sm ring-1 ${
                  a.read ? "bg-white/60 ring-ink-200" : emergencyAlert(a, log.data?.entries ?? []) ? "bg-red-50 ring-red-300" : "bg-tide-50 ring-tide-500/30"
                }`}
              >
                {entry ? (
                  <>
                    <div className="flex items-start justify-between gap-3">
                      <p className="font-semibold text-ink-900">
                        {startedEmergencyEntry(entry) ? "An emergency was started for you" : "Your emergency card was opened"}
                      </p>
                      <div className="shrink-0 text-right text-xs text-ink-500">
                        <p>{formatTime(a.at)}</p>
                        <p>{formatDate(a.at)}</p>
                      </div>
                    </div>
                    <p className="text-sm text-ink-700">
                      By <span className="font-semibold text-ink-900">{entry.actor_label}</span>
                      {entry.via ? ` with ${entry.via}'s QR code` : ""}
                    </p>
                    <AccessFacts e={entry} />
                  </>
                ) : (
                  <>
                    <p>{a.message}</p>
                    <p className="text-xs text-ink-500">{formatDateTime(a.at)}</p>
                  </>
                )}
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  {!a.read && <button className="btn-light" onClick={() => update(a, { read: true })}>Mark as read</button>}
                  {a.reported ? (
                    <span className="chip bg-ink-100 text-ink-700">Reported. Nobody reviews reports in this prototype yet.</span>
                  ) : (
                    <button className="btn-light" onClick={() => update(a, { reported: true })}>Report this access</button>
                  )}
                </div>
                {entry && !a.reported && <p className="mt-1 text-xs text-ink-500">Not expected? Report it.</p>}
              </li>
            );
          })}
        </ul>
      </div>

      <div className="card">
        <div className="mb-2">
          <h2 className="section-title">Access log</h2>
          {log.data && (
            <>
              <p className={`flex items-center gap-1.5 text-sm font-medium ${log.data.chain_intact ? "text-green-800" : "text-red-700"}`}>
                {log.data.chain_intact ? (
                  <>
                    <ShieldCheck size={16} aria-hidden /> No changes found in this log
                  </>
                ) : (
                  <>
                    <AlertTriangle size={16} aria-hidden /> This log has been changed
                  </>
                )}
              </p>
              <Disclosure label="More info">
                <p className="pb-2 text-sm text-ink-600">
                  Each log line is linked to the one before it. If an old line were changed or removed, this check would show it.
                </p>
              </Disclosure>
            </>
          )}
        </div>
        <ErrorNote error={log.error} />
        {log.data?.entries.length === 0 && <p className="text-sm text-ink-500">Nobody has opened your emergency card.</p>}
        <ol className="divide-y divide-ink-100">
          {log.data?.entries.map((e) => (
            <li key={e.id} className="py-3">
              <div className="flex items-baseline justify-between gap-3">
                <p className="flex min-w-0 items-center gap-1.5 font-semibold text-ink-900">
                  {e.reason === "emergency" && <Siren size={14} className="shrink-0 text-red-700" aria-label="Emergency" />}
                  <span className="min-w-0">{e.actor_label}</span>
                </p>
                <span className="shrink-0 text-sm text-ink-500">{formatDate(e.at)}</span>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm text-ink-700">{accessWhat(e)}</p>
                <span className="shrink-0 text-xs text-ink-500">{formatTime(e.at)}</span>
              </div>
              <AccessFacts e={e} />
            </li>
          ))}
        </ol>
      </div>

      <button className="card flex w-full items-center gap-3 p-4 text-left transition hover:bg-white/85" onClick={() => go("/people")}>
        <span className="tile"><HeartHandshake size={20} aria-hidden /></span>
        <span className="min-w-0 flex-1 font-semibold">My trusted people</span>
        <ChevronRight className="shrink-0 text-ink-500" aria-hidden />
      </button>
    </div>
  );
}
