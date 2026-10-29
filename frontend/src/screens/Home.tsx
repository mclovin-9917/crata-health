import {
  AlertTriangle, BellRing, ChevronRight, ClipboardCheck, ClipboardList, Cpu, FileText, FolderHeart, HeartHandshake, MessageCircleQuestion, Mic, Plus, ShieldCheck, Siren,
  TrendingUp, Users, Wrench,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, type ReceivedInvite, type Visit } from "../api";
import { useAccount, useMe } from "../App";
import { ErrorNote, PatientBasics, VisitSummary } from "../components";
import { emergencyAlert, formatDate, go, openedCard, useLoad } from "../lib";

/** The hub: every other screen opens from here (no menu bar). */
export default function HomeScreen() {
  const me = useMe();
  const { me: account } = useAccount();
  const record = useLoad(() => api.record(me.id), [me.id]);
  const alerts = useLoad(() => api.alerts(), [me.id]);
  const log = useLoad(() => api.accessLog(me.id), [me.id]);
  const trustedBy = useLoad(() => api.trustedBy(me.id), [me.id]);
  const invites = useLoad(() => api.myInvites(), [me.id]);
  const myPeople = useLoad(() => api.trusted(me.id), [me.id]);

  const unreadAlerts = alerts.data?.filter((a) => !a.read) ?? [];
  const unread = unreadAlerts.length;
  // Red only when one of them is the emergency card being opened.
  const urgent = unreadAlerts.some((a) => emergencyAlert(a, log.data?.entries ?? []));
  const visits = record.data?.visits.filter((v) => v.encounter_id).length ?? 0;
  const latestVisit = record.data?.visits.find((v) => v.encounter_id) ?? null;
  // The banner opens the newest visit with suggestions still waiting, so it counts that visit's only.
  const waitingVisit = record.data?.visits.find((v) => v.encounter_id && v.pending > 0) ?? null;
  const waiting = waitingVisit?.pending ?? 0;
  const showBanner = waiting > 0 && waitingVisit !== null;
  // A trusted person starting an emergency sees the basics only; that is not the card being opened.
  const lastOpening = (log.data?.entries ?? []).filter(openedCard).sort((a, b) => b.at.localeCompare(a.at))[0] ?? null;
  // Show the sections only once their data is in (or failed), so nothing pops in and shifts the page.
  const ready = [record, alerts, log, trustedBy, invites, myPeople].every((l) => l.data !== null || l.error !== null);
  // A person with no record of their own who is a trusted person for others sees that card first.
  const trustedFirst = !!trustedBy.data?.length && record.data?.visits !== undefined && visits === 0;
  const trustedCard = !!trustedBy.data?.length && (
    <button className="card flex w-full items-center gap-4 p-4 text-left transition hover:bg-white/85" onClick={() => go("/trusted")}>
      <span className="tile"><Users size={20} aria-hidden /></span>
      <span className="min-w-0 flex-1">
        <span className="block font-bold">People who trust me</span>
        <span className="text-sm text-ink-600">
          {trustedBy.data.map((p) => p.name).join(", ")} added you as a trusted person
        </span>
      </span>
      <ChevronRight className="text-ink-500" aria-hidden />
    </button>
  );

  return (
    <div className="space-y-6">
      <section className="hero">
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tight">{me.name}</h1>
        {record.data && (
          <p className="mt-1 text-white">
            {visits} visit{visits === 1 ? "" : "s"} in your record
          </p>
        )}
        <PatientBasics patient={me} tone="hero" />
      </section>

      {!ready ? (
        <p className="text-ink-500" role="status">Loading…</p>
      ) : (
      <>
      {/* An unread alert comes first, above everything else; red only if the card was opened. */}
      {unread > 0 && (
        <button
          className={`card flex w-full items-center gap-3 p-4 text-left ring-1 transition hover:bg-white/90 ${urgent ? "ring-red-300" : "ring-tide-500/30"}`}
          onClick={() => go("/access")}
        >
          <span className={`tile ${urgent ? "[background:var(--grad-emergency)]" : ""}`}><BellRing size={20} aria-hidden /></span>
          <span>
            <span className={`block font-semibold ${urgent ? "text-red-800" : "text-ink-900"}`}>
              {unread} new alert{unread > 1 ? "s" : ""}
            </span>
            <span className="text-sm text-ink-600">Tap to see what happened.</span>
          </span>
        </button>
      )}

      {/* A trusted person with no record of their own starts from the people who trust them. */}
      {trustedFirst && trustedCard}

      {invites.data?.map((inv) => (
        <Invitation key={inv.id} invite={inv} onAnswered={() => { invites.reload(); trustedBy.reload(); }} />
      ))}

      {showBanner && (
        <section
          className="flex flex-wrap items-center gap-3 rounded-2xl bg-amber-50 px-4 py-3 text-amber-900 ring-1 ring-amber-300"
          aria-label="Suggestions to review"
        >
          <ClipboardCheck size={20} className="shrink-0" aria-hidden />
          <p className="min-w-0 flex-1 font-semibold">
            {waiting} suggestion{waiting === 1 ? "" : "s"} to review
          </p>
          <button className="btn" onClick={() => go(`/encounters/${waitingVisit?.encounter_id}`)}>Review suggestions</button>
        </section>
      )}

      <div className="grid grid-cols-2 gap-3">
        <BigAction
          onClick={() => go("/add")}
          icon={<Plus size={24} aria-hidden />}
          tile="[background:var(--grad-action)]"
          title="Add a visit"
          sub="Recording, prescription or bill"
        />
        <BigAction
          onClick={() => go("/emergency")}
          icon={<Siren size={24} aria-hidden />}
          tile="[background:var(--grad-emergency)]"
          title="Emergency card"
          sub="For a doctor, with a QR code"
          danger
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <BigAction
          onClick={() => go("/prepare")}
          icon={<ClipboardList size={24} aria-hidden />}
          tile=""
          title="Before a visit"
          sub="Get a summary for your doctor"
        />
        <BigAction
          onClick={() => go("/ask")}
          icon={<MessageCircleQuestion size={24} aria-hidden />}
          tile=""
          title="Ask about my health"
          sub="Your record, food and daily life"
        />
      </div>

      <button className="card flex w-full items-center gap-4 p-4 text-left transition hover:bg-white/85" onClick={() => go("/record")}>
        <span className="tile"><FolderHeart size={20} aria-hidden /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-lg font-bold tracking-tight">My record</span>
          <span className="text-sm text-ink-600">Your facts, visits and timeline</span>
        </span>
        <ChevronRight className="text-ink-500" aria-hidden />
      </button>

      <button className="card flex w-full items-center gap-4 p-4 text-left transition hover:bg-white/85" onClick={() => go("/numbers")}>
        <span className="tile"><TrendingUp size={20} aria-hidden /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-lg font-bold tracking-tight">My health numbers</span>
          <span className="text-sm text-ink-600">Blood pressure, sugar, cholesterol, weight and more, over time</span>
        </span>
        <ChevronRight className="text-ink-500" aria-hidden />
      </button>

      <section className="space-y-3">
        <h2 className="section-title">Latest visit</h2>
        <ErrorNote error={record.error} />
        {record.data && !latestVisit && (
          <div className="card text-sm text-ink-500">No visits yet. Add one: a recording, prescription or bill.</div>
        )}
        {latestVisit && <LatestVisit visit={latestVisit} bannerShown={showBanner} />}
      </section>

      <section className="card flex items-start gap-3 p-4" aria-label="How your visit is read">
        <span className="tile"><Cpu size={20} aria-hidden /></span>
        <div className="min-w-0">
          <h2 className="font-bold tracking-tight">How your visit is read</h2>
          <p className="mt-1 text-sm text-ink-600">
            AI reads your photos, PDFs and recordings (NVIDIA Nemotron, with Nebius as backup) and suggests facts. You
            confirm allergies, medicines, diagnoses and events. The rest is added for you, and you can change it.
          </p>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="section-title">Safety and sharing</h2>
        <ErrorNote error={log.error} />
        <div className="card divide-y divide-ink-100 p-0">
          <button
            className="flex min-h-14 w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-white/60"
            onClick={() => go("/access")}
          >
            <span className="tile size-9"><ShieldCheck size={18} aria-hidden /></span>
            <span className="min-w-0 flex-1 text-sm">
              <span className="font-semibold">Who opened my emergency card: </span>
              <span className="text-ink-700">
                {!log.data
                  ? "…"
                  : lastOpening
                    ? `${lastOpening.actor_label}, ${new Date(lastOpening.at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`
                    : "Nobody yet"}
              </span>
              {log.data && !log.data.chain_intact && (
                <span className="mt-0.5 flex items-center gap-1 font-semibold text-red-700">
                  <AlertTriangle size={14} aria-hidden /> This log has been changed
                </span>
              )}
            </span>
            <ChevronRight className="shrink-0 text-ink-500" aria-hidden />
          </button>
          <button
            className="flex min-h-14 w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-white/60"
            onClick={() => go("/people")}
          >
            <span className="tile size-9"><HeartHandshake size={18} aria-hidden /></span>
            <span className="min-w-0 flex-1 text-sm">
              <span className="font-semibold">My trusted people: </span>
              <span className="text-ink-700">
                {myPeople.data?.length ? myPeople.data.map((p) => p.name).join(", ") : "None yet"}
              </span>
            </span>
            <ChevronRight className="shrink-0 text-ink-500" aria-hidden />
          </button>
        </div>
      </section>

      {!trustedFirst && trustedCard}

      {account.admin && (
        <button className="btn-light w-full" onClick={() => go("/meta")}>
          <Wrench size={16} aria-hidden /> Under the hood (admin)
        </button>
      )}
      </>
      )}
    </div>
  );
}

/** Someone asked the signed-in person to be their trusted person: accept or decline. */
function Invitation({ invite, onAnswered }: { invite: ReceivedInvite; onAnswered: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const answer = async (accept: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await (accept ? api.acceptInvite(invite.id) : api.declineInvite(invite.id));
      onAnswered();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <section className="card space-y-3 p-4 ring-1 ring-tide-500/30" aria-label="Invitation">
      <div className="flex items-start gap-3">
        <span className="tile"><HeartHandshake size={20} aria-hidden /></span>
        <div>
          <p className="font-bold">{invite.from_name} asked you to be their trusted person</p>
          <p className="text-sm text-ink-600">
            In an emergency you can start one for them. You see only their name, blood group and allergies, and get a
            QR code to show a doctor. You never see their record.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={busy} onClick={() => answer(true)}>Accept invitation</button>
        <button className="btn-light" disabled={busy} onClick={() => answer(false)}>Decline invitation</button>
      </div>
      <ErrorNote error={error} />
    </section>
  );
}

function BigAction({ onClick, icon, tile, title, sub, danger }: {
  onClick: () => void;
  icon: ReactNode;
  tile: string;
  title: string;
  sub: string;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`card flex flex-col items-start gap-3 p-4 text-left transition hover:-translate-y-0.5 hover:bg-white/85 active:scale-[0.98] ${
        danger ? "ring-1 ring-red-200" : ""
      }`}
    >
      <span className={`tile size-10 sm:size-12 rounded-2xl ${tile}`}>{icon}</span>
      <span>
        <span className={`block text-base sm:text-lg font-bold leading-tight tracking-tight ${danger ? "text-red-800" : ""}`}>{title}</span>
        <span className="mt-0.5 block text-sm text-ink-600">{sub}</span>
      </span>
    </button>
  );
}

function sourcesLine(v: Visit): string {
  const n = (t: string) => v.sources.filter((s) => s.source_type === t).length;
  const docs = n("image");
  return [
    n("audio") ? "Recording" : "",
    docs ? `${docs} document${docs > 1 ? "s" : ""}` : "",
    n("text") ? "typed notes" : "",
  ].filter(Boolean).join(", ").replace(/^./, (c) => c.toUpperCase());
}

/** The newest visit, summed up from its confirmed facts. */
function LatestVisit({ visit, bannerShown }: { visit: Visit; bannerShown: boolean }) {
  const open = () => go(`/encounters/${visit.encounter_id}`);
  const confirmed = visit.entries.length;
  return (
    <div className="card space-y-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          {visit.title ? (
            <>
              <p className="text-lg font-bold tracking-tight">{visit.title}</p>
              <p className="text-sm text-ink-600">
                {[formatDate(visit.visit_date), visit.facility].filter(Boolean).join(", ")}
              </p>
            </>
          ) : (
            <p className="text-lg font-bold tracking-tight">{formatDate(visit.visit_date)}</p>
          )}
          <p className="flex items-center gap-1.5 text-sm text-ink-500">
            {visit.sources.some((s) => s.source_type === "audio") ? <Mic size={14} aria-hidden /> : <FileText size={14} aria-hidden />}
            {sourcesLine(visit)}
          </p>
        </div>
        {visit.status === "processing" ? (
          <span className="chip bg-tide-50 text-tide-800">Being read…</span>
        ) : visit.status === "failed" ? (
          <span className="chip bg-red-50 text-red-800">Could not be read</span>
        ) : visit.pending > 0 ? (
          <span className="chip bg-amber-100 text-amber-900">{visit.pending} to review</span>
        ) : (
          <span className="chip bg-green-50 text-green-800">Reviewed</span>
        )}
      </div>

      {confirmed > 0 ? (
        <VisitSummary entries={visit.entries} />
      ) : (
        <p className="text-sm text-ink-600">
          {visit.pending > 0
            ? "Nothing confirmed yet. Review the suggestions to add them."
            : "Nothing from this visit is in your record."}
        </p>
      )}

      {visit.pending > 0 && bannerShown ? (
        // The waiting banner above already carries the Review button; keep only a quiet way in.
        <button className="link-quiet" onClick={open}>Open this visit</button>
      ) : (
        <button className={visit.pending > 0 ? "btn w-full" : "btn-light w-full"} onClick={open}>
          {visit.pending > 0 ? `Review ${visit.pending} suggestion${visit.pending > 1 ? "s" : ""}` : "Open this visit"}
        </button>
      )}
    </div>
  );
}
