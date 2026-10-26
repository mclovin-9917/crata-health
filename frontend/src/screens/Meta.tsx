import { api } from "../api";
import { ErrorNote } from "../components";
import { stepLabels, useLoad } from "../lib";

function lastEncounterId(): string | null {
  try {
    return localStorage.getItem("healthrecord.lastEncounter");
  } catch {
    return null;
  }
}

export default function MetaScreen() {
  const meta = useLoad(() => api.meta(), []);
  const lastId = lastEncounterId();
  const last = useLoad(() => (lastId ? api.encounter(lastId) : Promise.resolve(null)), [lastId]);

  return (
    <div className="space-y-4">
      <h1 className="title">Under the hood</h1>
      <ErrorNote error={meta.error} />
      {meta.data && (
        <div className="card text-sm">
          <p><span className="font-medium">Reading audio and documents:</span> {meta.data.perceive.model} on {meta.data.perceive.provider === "nvidia" ? "NVIDIA build" : "Nebius Token Factory"}</p>
          <p><span className="font-medium">Finding facts and checking them:</span> {meta.data.reason.model} on {meta.data.reason.provider === "nebius" ? "Nebius Token Factory" : "NVIDIA build"}</p>
        </div>
      )}
      {last.data && (
        <div className="card text-sm">
          <h2 className="section-title mb-2">Last visit read</h2>
          <ul>
            {Object.entries(last.data.step_seconds).map(([step, s]) => (
              <li key={step}>
                {stepLabels[step] ?? step}: {s} s
                {last.data?.models_used?.[step] && (
                  <span className="text-ink-500">, {last.data.models_used[step].join(", ")}</span>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-ink-600">
            Left out by the quote check: {last.data.quote_failures.length}
          </p>
        </div>
      )}
    </div>
  );
}
