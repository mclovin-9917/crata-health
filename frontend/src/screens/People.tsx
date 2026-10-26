import { Clock, Send, UserMinus, X } from "lucide-react";
import { useState } from "react";
import { api, type SentInvite, type TrustedPerson } from "../api";
import { useMe } from "../App";
import { ErrorNote } from "../components";
import { formatDate, useLoad } from "../lib";

/** The patient's trusted people: invite by phone number, cancel an invitation, remove someone.
 * Nobody becomes a trusted person until they accept (decided 2026-10-15). */
export default function PeopleScreen() {
  const me = useMe();
  const people = useLoad(() => api.trusted(me.id), [me.id]);
  const invites = useLoad(() => api.sentInvites(me.id), [me.id]);
  const [phone, setPhone] = useState("");
  const [removing, setRemoving] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const invite = () => run(async () => {
    const inv = await api.invite(me.id, phone);
    invites.setData((prev) => [...(prev ?? []), inv]);
    setSent(inv.phone);
    setPhone("");
  });
  const cancel = (inv: SentInvite) => run(async () => {
    await api.cancelInvite(me.id, inv.id);
    invites.setData((prev) => (prev ?? []).filter((i) => i.id !== inv.id));
  });
  const remove = (p: TrustedPerson) => run(async () => {
    people.setData(await api.removeTrusted(me.id, p.id));
    setRemoving(null);
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="title">My trusted people</h1>
        <p className="mt-1 text-ink-600">
          A trusted person can start an emergency for you. They see only your name, blood group and allergies, and get
          a QR code to show a doctor. They never see your record. You get an alert each time.
        </p>
      </div>

      <section className="card space-y-3">
        <ErrorNote error={people.error} />
        {people.data?.length === 0 && <p className="text-sm text-ink-500">None yet. Invite someone below.</p>}
        <ul className="divide-y divide-ink-100">
          {people.data?.map((p) => (
            <li key={p.id} className="space-y-2 py-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-semibold">{p.name}</span>
                {removing !== p.id && (
                  <button className="link-quiet ml-auto" onClick={() => setRemoving(p.id)} aria-label={`Remove ${p.name}`}>
                    <UserMinus size={16} aria-hidden /> Remove
                  </button>
                )}
              </div>
              {removing === p.id && (
                <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-ink-50/80 p-3 text-sm ring-1 ring-ink-100">
                  <span className="flex-1">Remove {p.name}? They get an alert and can no longer start an emergency for you.</span>
                  <button className="btn" disabled={busy} onClick={() => remove(p)}>Remove</button>
                  <button className="btn-light" disabled={busy} onClick={() => setRemoving(null)}>Cancel</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="card space-y-3">
        <h2 className="section-title">Invite someone</h2>
        <p className="text-sm text-ink-600">
          Enter their mobile number. They see your invitation when they sign in with it, and can accept or decline. It
          expires after 14 days.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="block min-w-48 flex-1" htmlFor="invite-phone">
            <span className="label">Mobile number</span>
            <input id="invite-phone" className="input" type="tel" inputMode="tel" placeholder="98000 00000" value={phone}
              onChange={(e) => { setPhone(e.target.value); setSent(null); }} />
          </label>
          <button className="btn" disabled={busy || phone.trim().length < 6} onClick={invite}>
            <Send size={16} aria-hidden /> Send invitation
          </button>
        </div>
        {sent && <p className="text-sm text-green-800">Invitation sent to {sent}.</p>}
        <ErrorNote error={error} />

        {!!invites.data?.length && (
          <div className="space-y-2 pt-2">
            <h3 className="text-sm font-semibold text-ink-600">Waiting for an answer</h3>
            <ul className="divide-y divide-ink-100 rounded-2xl ring-1 ring-ink-100">
              {invites.data.map((inv) => (
                <li key={inv.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
                  <span className="font-semibold">{inv.phone}</span>
                  <span className="flex items-center gap-1 text-ink-500">
                    <Clock size={14} aria-hidden /> Expires {formatDate(inv.expires_at)}
                  </span>
                  <button className="btn-light ml-auto py-1" disabled={busy} onClick={() => cancel(inv)}
                    aria-label={`Cancel the invitation to ${inv.phone}`}>
                    <X size={14} aria-hidden /> Cancel
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}
