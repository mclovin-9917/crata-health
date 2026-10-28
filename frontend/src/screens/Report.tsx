import { AlertTriangle } from "lucide-react";
import { api } from "../api";
import { useMe } from "../App";
import { ErrorNote, rangeText } from "../components";
import { editedByPatient, formatDate, go, useLoad } from "../lib";
import { confirmedValuesOf, outsideRange, reportDate } from "../results";

// On a phone each row stacks (name, then "Result: ..." lines); from sm up it is a plain table.
// The roles keep the table semantics when the display type is changed.
const CELL = "block px-0 py-0.5 before:mr-1 before:text-xs before:font-semibold before:text-ink-500 before:content-[attr(data-label)] max-sm:empty:hidden sm:table-cell sm:px-3 sm:py-2 sm:before:hidden";

/** One test as a lab report: each value with its unit and the range printed on the report, and
 * whether it is above or below that range. It does not say what a value means. The way back is
 * the shell's Home link, as on the other screens. */
export default function ReportScreen({ testId }: { testId: string }) {
  const me = useMe();
  const { data, error } = useLoad(() => api.record(me.id), [me.id]);

  if (error) return <ErrorNote error={error} />;
  if (!data) return <p className="text-ink-500">Loading…</p>;

  // Any confirmed test of any visit, not only the newest of its name.
  const entries = data.visits.flatMap((v) => v.entries);
  const test = entries.find((e) => e.id === testId && e.kind === "test" && e.status === "confirmed");
  if (!test) return <div className="card text-sm text-ink-600">This report is not in your record.</div>;

  const values = confirmedValuesOf(entries, test);
  const visit = data.visits.find((v) => v.encounter_id === test.encounter_id);
  const on = reportDate(values);
  const visitDay = visit?.visit_date?.slice(0, 10) ?? null;
  // The visit is named only when it is a different day from the report.
  const showVisit = !!visit?.encounter_id && (!on || on.slice(0, 10) !== visitDay);
  // A date column when the values of one report carry different dates.
  const dates = new Set(values.map((v) => v.result?.measured_on?.slice(0, 10) ?? ""));
  const dateColumn = dates.size > 1;
  // A scan report (CT, MRI, X-ray, ultrasound): every value is a line of words, so no table.
  const inWords = values.length > 0 && values.every((v) => v.result?.value === null);
  const headers = ["Measure", "Result", "Unit", "Printed range", ...(dateColumn ? ["Date"] : []), "Compared with range"];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="title">{test.label}</h1>
        <p className="mt-1 text-sm text-ink-600">
          {on && <span>Report of {formatDate(on)}.</span>}
          {on && showVisit && " "}
          {showVisit && (
            <>
              From the visit of{" "}
              <button className="font-semibold text-tide-700 underline" onClick={() => go(`/encounters/${visit!.encounter_id}`)}>
                {visit!.visit_date ? formatDate(visit!.visit_date) : "an unknown date"}
              </button>
              .
            </>
          )}
          {!on && !showVisit && "Added by you"}
        </p>
      </div>

      <div className="card">
        {values.length === 0 ? (
          <p className="text-sm text-ink-600">No results in this report yet.</p>
        ) : inWords ? (
          <section aria-labelledby="report-findings">
            <h2 id="report-findings" className="text-xs font-bold tracking-wider text-ink-600 uppercase">Findings</h2>
            <ol role="list" className="mt-2 divide-y divide-ink-100 text-sm text-ink-900">
              {values.map((v) => {
                const r = v.result!;
                const text = r.value_text ?? v.label;
                const range = rangeText(r);
                // Scan lines already start with their key words; lab words ("Non-reactive") need the name.
                const strip = (s: string) => s.replace(/^[^\p{L}\p{N}]+/u, "").toLowerCase();
                const startsWithLabel = !!v.label && strip(text).startsWith(strip(v.label));
                return (
                  <li key={v.id} className="py-2 first:pt-0 last:pb-0">
                    {startsWithLabel ? text : (
                      <>
                        <span className="font-semibold">{v.label}</span>: {text}
                      </>
                    )}
                    {range && <span className="ml-2 text-xs text-ink-500">printed range: {range}</span>}
                    {dateColumn && r.measured_on && <span className="ml-2 text-xs text-ink-500">{formatDate(r.measured_on)}</span>}
                    {editedByPatient(v) && <span className="ml-2 text-xs font-normal text-amber-700">Edited by you</span>}
                  </li>
                );
              })}
            </ol>
          </section>
        ) : (
          <table className="block w-full text-left text-sm sm:table" role="table" aria-label={`${test.label}, results`}>
            <thead className="sr-only sm:table-header-group sm:not-sr-only" role="rowgroup">
              <tr className="block sm:table-row sm:border-b sm:border-ink-200" role="row">
                {headers.map((h) => (
                  <th key={h} scope="col" role="columnheader" className="px-3 py-2 text-xs font-bold tracking-wider text-ink-600 uppercase sm:table-cell">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="block divide-y divide-ink-100 sm:table-row-group" role="rowgroup">
              {values.map((v) => {
                const r = v.result!;
                const side = outsideRange(r);
                return (
                  <tr key={v.id} className="block py-3 first:pt-0 last:pb-0 sm:table-row sm:py-0" role="row">
                    <th scope="row" role="rowheader" className="block py-0.5 text-base font-semibold text-ink-900 sm:table-cell sm:px-3 sm:py-2 sm:text-sm">
                      {v.label}
                      {editedByPatient(v) && <span className="ml-2 text-xs font-normal text-amber-700">Edited by you</span>}
                    </th>
                    <td role="cell" data-label="Result:" className={`${CELL} font-semibold tabular-nums`}>
                      {r.value !== null ? r.value : r.value_text}
                    </td>
                    <td role="cell" data-label="Unit:" className={CELL}>{r.value !== null ? r.unit : null}</td>
                    <td role="cell" data-label="Printed range:" className={`${CELL} tabular-nums`}>{rangeText(r)}</td>
                    {dateColumn && (
                      <td role="cell" data-label="Date:" className={CELL}>{r.measured_on ? formatDate(r.measured_on) : null}</td>
                    )}
                    <td role="cell" className="block py-0.5 max-sm:empty:hidden sm:table-cell sm:px-3 sm:py-2">
                      {side && (
                        <span className="chip bg-amber-100 text-amber-900">
                          <AlertTriangle size={12} aria-hidden /> {side === "above" ? "Above range" : "Below range"}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <p className="text-xs text-ink-500">
        {inWords
          ? "Read from the report as printed. Ask your doctor what these findings mean for you."
          : "Above or below means outside the range printed on the report. Ranges differ between labs. Ask your doctor what a number means for you."}
      </p>
    </div>
  );
}
