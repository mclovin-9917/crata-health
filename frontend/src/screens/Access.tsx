import { AlertTriangle, ChevronRight, HeartHandshake, ShieldCheck } from "lucide-react";
import { api, type Alert } from "../api";
import { useMe } from "../App";
import { Disclosure, ErrorNote } from "../components";
import { accessLine, emergencyAlert, formatDateTime, go, useLoad } from "../lib";

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
          {alerts.data?.map((a) => (
            <li
              key={a.id}
              className={`rounded-xl p-3 text-sm ring-1 ${
                a.read ? "bg-white/60 ring-ink-200" : emergencyAlert(a, log.data?.entries ?? []) ? "bg-red-50 ring-red-300" : "bg-tide-50 ring-tide-500/30"
              }`}
            >
              <p>{a.message}</p>
              <p className="text-xs text-ink-500">{formatDateTime(a.at)}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {!a.read && <button className="btn-light" onClick={() => update(a, { read: true })}>Mark as read</button>}
                {a.reported ? (
                  <span className="chip bg-ink-100 text-ink-700">Reported. Nobody reviews reports in this prototype yet.</span>
                ) : (
                  <button className="btn-light" onClick={() => update(a, { reported: true })}>Report this access</button>
                )}
              </div>
            </li>
          ))}
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
        <ol className="space-y-1 text-sm">
          {log.data?.entries.map((e) => (
            <li key={e.id}>
              <span className="text-ink-500">{formatDateTime(e.at)}</span>. <span className="font-semibold">{e.actor_label}</span>
              {e.actor_registry_id ? ` (registry number ${e.actor_registry_id}, not verified)` : ""}. {accessLine(e)}.
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
