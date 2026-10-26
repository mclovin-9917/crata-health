import { AlertTriangle, ChevronLeft, ChevronRight, HeartPulse, LogOut, ScanLine } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, SIGNED_OUT, type MeView, type Patient } from "./api";
import { go, takeReturn, useRoute } from "./lib";
import { isSettled, visitMedia } from "./media";
import AccessScreen from "./screens/Access";
import AddScreen from "./screens/Add";
import PeopleScreen from "./screens/People";
import DoctorScreen from "./screens/Doctor";
import EmergencyScreen from "./screens/Emergency";
import EncounterScreen from "./screens/Encounter";
import HomeScreen from "./screens/Home";
import MetaScreen from "./screens/Meta";
import NumbersScreen from "./screens/Numbers";
import RecordScreen from "./screens/Record";
import ReportScreen from "./screens/Report";
import ScanScreen from "./screens/Scan";
import SignInScreen from "./screens/SignIn";
import TrustedScreen from "./screens/Trusted";

// Who is signed in (the server's session cookie decides; the page cannot read it).
interface AccountState { me: MeView; signOut: () => void }
const AccountContext = createContext<AccountState | null>(null);
export const useAccount = () => useContext(AccountContext)!;

// A patient account's own person: the record every patient screen shows.
const MeContext = createContext<Patient | null>(null);
export const useMe = () => useContext(MeContext)!;

export default function App() {
  const route = useRoute();
  const [me, setMe] = useState<MeView | null | undefined>(undefined); // undefined: still asking
  const [person, setPerson] = useState<Patient | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api.auth.me().then(setMe, (e: Error) => (/sign in/i.test(e.message) ? setMe(null) : setError(e.message)));
  }, []);
  useEffect(load, [load]);

  // A session that ends while the app is open (expired, or signed out on another tab).
  useEffect(() => {
    const onSignedOut = () => setMe(null);
    window.addEventListener(SIGNED_OUT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT, onSignedOut);
  }, []);

  useEffect(() => {
    setPerson(null);
    if (!me?.person_id) return;
    api.patient(me.person_id).then(setPerson, (e: Error) => setError(e.message));
    // Delete the browser's copy of every visit that has been fully reviewed (or is gone).
    visitMedia.sweep((id) =>
      api.encounter(id).then(isSettled, (e: Error) => /not found/i.test(e.message)),
    );
  }, [me]);

  const signOut = () => {
    api.auth.logout().finally(() => {
      setMe(null);
      go("/");
    });
  };

  // The doctor's view is opened from the QR code, signed in or not: no patient shell around it.
  // #/doctor/<patient>: the patient's own card QR. #/doctor/<patient>/<code>: a trusted
  // person's short-lived code.
  if (route[0] === "doctor") return <DoctorScreen patientId={route[1]} code={route[2]} />;

  if (error) {
    return (
      <div className="space-y-3 p-6">
        <p className="flex items-center gap-2 text-red-700" role="alert">
          <AlertTriangle size={16} className="shrink-0" aria-hidden /> Could not load the app. Check your connection and try again.
        </p>
        <button className="btn-light" onClick={load}>Try again</button>
      </div>
    );
  }
  if (me === undefined) return <p className="p-6 text-ink-500">Loading…</p>;
  const signedIn = (m: MeView) => {
    setMe(m);
    const back = takeReturn();
    if (back) window.location.hash = back; // e.g. the patient's QR page a doctor came from
  };
  if (me === null) return <Shell><SignInScreen onSignedIn={signedIn} /></Shell>;

  const account = { me, signOut };
  if (me.role === "doctor") {
    return (
      <AccountContext.Provider value={account}>
        <Shell account={account}>
          {route[0] === "scan" ? (
            <>
              <BackHome />
              <ScanScreen />
            </>
          ) : (
            <DoctorHome me={me} />
          )}
        </Shell>
      </AccountContext.Provider>
    );
  }
  if (!person) return <p className="p-6 text-ink-500">Loading…</p>;

  const here = route[0] ?? "";
  return (
    <AccountContext.Provider value={account}>
      <MeContext.Provider value={person}>
        <Shell account={account}>
          {/* No menu bar: Home is the hub, every other screen has a way back to it. */}
          {here !== "" && <BackHome />}
          <Screen route={route} admin={me.admin} />
        </Shell>
      </MeContext.Provider>
    </AccountContext.Provider>
  );
}

function BackHome() {
  return (
    <button
      className="-ml-1 mb-4 inline-flex min-h-11 items-center gap-0.5 rounded-full py-1 pr-3 pl-1 text-sm font-semibold text-tide-700 transition hover:bg-white/70"
      onClick={() => go("/")}
    >
      <ChevronLeft size={18} aria-hidden /> Back to Home
    </button>
  );
}

function Shell({ account, children }: { account?: AccountState; children: ReactNode }) {
  return (
    <>
      <header className="sticky top-0 z-20 border-b border-white/70 bg-white/60 backdrop-blur-xl backdrop-saturate-150">
        <div className="mx-auto flex max-w-3xl items-center gap-x-3 px-4 py-3 sm:flex-wrap sm:gap-y-2">
          <a href="#/" className="flex items-center gap-2 font-bold tracking-tight text-ink-900">
            <span className="tile size-8 rounded-lg"><HeartPulse size={18} aria-hidden /></span>
            Crata Health
          </a>
          <span className="chip shrink-0 bg-ink-100 text-ink-700"><span>Prototype<span className="hidden sm:inline">,&nbsp;made-up data</span></span></span>
          {account && (
            <div className="ml-auto flex shrink-0 items-center gap-2 text-sm">
              <span className="hidden font-semibold text-ink-800 sm:inline">{account.me.name}</span>
              {account.me.demo && <span className="chip hidden bg-ink-100 text-ink-700 sm:inline-flex">Demo</span>}
              <button className="btn-light min-h-11 min-w-11 px-3 py-1.5" aria-label="Sign out" onClick={account.signOut}>
                <LogOut size={16} aria-hidden /> <span className="hidden sm:inline">Sign out</span>
              </button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 pt-6 pb-20">{children}</main>
    </>
  );
}

function Screen({ route, admin }: { route: string[]; admin: boolean }) {
  switch (route[0]) {
    case "add":
      return <AddScreen stayId={route[1]} testId={route[2]} />;
    case "encounters":
      return <EncounterScreen encounterId={route[1]} />;
    case "record":
      return <RecordScreen />;
    case "numbers":
      return <NumbersScreen />;
    case "reports":
      return <ReportScreen testId={route[1]} />;
    case "emergency":
      return <EmergencyScreen />;
    case "access":
      return <AccessScreen />;
    case "trusted":
      return <TrustedScreen />;
    case "people":
      return <PeopleScreen />;
    case "meta":
      return admin ? <MetaScreen /> : <AdminOnly />;
    default:
      return <HomeScreen />;
  }
}

function AdminOnly() {
  return (
    <div className="card space-y-2">
      <h1 className="section-title">This page is for admins</h1>
      <p className="text-sm text-ink-600">It shows which models ran and how long each step took.</p>
    </div>
  );
}

/** A doctor's start page. Doctors reach a patient through the patient's QR code. */
function DoctorHome({ me }: { me: MeView }) {
  return (
    <div className="space-y-4">
      <div className="hero space-y-1">
        <p className="text-sm font-semibold text-white">Doctor</p>
        <h1 className="text-3xl font-bold tracking-tight">{me.name}</h1>
        <p className="text-white">
          Registry number {me.registry_id ?? "not given"} (not verified in this prototype)
        </p>
      </div>
      <button className="card flex w-full items-center gap-3 text-left transition hover:bg-white" onClick={() => go("/scan")}>
        <span className="tile [background:var(--grad-emergency)]"><ScanLine size={20} aria-hidden /></span>
        <span className="min-w-0 grow space-y-1">
          <span className="block text-lg font-semibold">Scan a QR code</span>
          <span className="block text-sm text-ink-700">
            Scan the QR code on a patient's emergency card, or one a trusted person shows you. Each opening is logged
            and the patient gets an alert.
          </span>
        </span>
        <ChevronRight size={20} className="shrink-0 text-ink-400" aria-hidden />
      </button>
    </div>
  );
}

