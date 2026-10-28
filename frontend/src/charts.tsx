import { useLayoutEffect, useRef, useState } from "react";
import type { NumberPoint, NumberRange, NumberSeries } from "./api";
import { formatDate } from "./lib";

/** A number as people write it: 13.2, 214, 185432, not 13.200000000000001. Only the noise a
 * float adds is cut off; a printed number is never rounded. */
export const shown = (n: number | null) => (n === null || !Number.isFinite(n) ? "" : String(Number(n.toFixed(6))));

/** "4 to 5.6", "up to 200", "from 40": the range as a report prints it. */
export function rangeWords(r: Pick<NumberRange, "low" | "high">): string | null {
  if (r.low !== null && r.high !== null) return `${shown(r.low)} to ${shown(r.high)}`;
  if (r.high !== null) return `up to ${shown(r.high)}`;
  if (r.low !== null) return `from ${shown(r.low)}`;
  return null;
}

const DAY = 86_400_000;
const time = (iso: string) => Date.parse(`${iso}T00:00:00`);

/** What the chart says in words, for a screen reader (the readings list below it is the full text). */
export function chartLabel(series: NumberSeries[]): string {
  return series
    .map((s) => {
      const live = s.points.filter((p) => !p.removed);
      if (!live.length) return `${s.name}: no readings`;
      const last = live[live.length - 1];
      return `${s.name}: ${live.length} reading${live.length === 1 ? "" : "s"}, from ${formatDate(live[0].on)} to ${formatDate(last.on)}, latest ${shown(last.value)}${last.unit ? ` ${last.unit}` : ""}`;
    })
    .join(". ");
}

/** The lines that have a printed range and readings still on the chart: each gets a band, and a
 * "Shaded" line under the big chart (the second one with a dashed edge). */
export const bandedSeries = (series: NumberSeries[]) =>
  series.filter((s) => s.range && rangeWords(s.range) && s.points.some((p) => !p.removed));

/** The part of a line's name that tells the lines of one card apart ("systolic"). */
export const lineName = (s: NumberSeries) => plainName(s.name.match(/\(([^)]+)\)\s*$/)?.[1] ?? s.name);

/** Common short names a lab report uses, said the way a patient would. */
const PLAIN: Record<string, string> = {
  hb: "Haemoglobin", hba1c: "HbA1c (3-month sugar)", fbs: "Fasting sugar", ppbs: "Sugar after a meal", rbs: "Random sugar",
  ldl: "LDL cholesterol", hdl: "HDL cholesterol", tsh: "Thyroid (TSH)", wbc: "White blood cells", rbc: "Red blood cells",
  sgpt: "Liver enzyme (SGPT)", alt: "Liver enzyme (ALT)", sgot: "Liver enzyme (SGOT)", ast: "Liver enzyme (AST)",
};

/** A raw key ("blood_pressure_systolic", "hb") shown as plain words; a name already in words is left alone. */
export function plainName(name: string): string {
  const known = PLAIN[name.trim().toLowerCase()];
  if (known) return known;
  if (!/[_]/.test(name) && name !== name.toLowerCase()) return name;
  const words = name.replace(/[_]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** What the shaded band means, one line per line that has one (the band itself carries no text).
 * The second band is drawn with a dashed edge, and its line says so. */
export function BandNotes({ series }: { series: NumberSeries[] }) {
  const several = series.length > 1;
  const banded = bandedSeries(series);
  if (!banded.length) return null;
  return (
    <div className="space-y-0.5 text-xs text-ink-600">
      {banded.map((s, i) => (
        <p key={`${s.key}-${s.unit}-band`}>
          {several ? `${lineName(s)} shaded band` : "Shaded band"}{i > 0 ? " (dashed edge)" : ""}: {rangeWords(s.range!)},
          printed on your {s.range!.on ? `${formatDate(s.range!.on)} ` : ""}report
        </p>
      ))}
    </div>
  );
}

// A line is teal, a second line (diastolic) blue; a dot outside its range is amber.
const LINE = ["text-tide-600", "text-lagoon-600"];
/** The colour class of the i-th line of a chart, for a legend that matches it. */
export const lineTone = (i: number) => LINE[i % LINE.length];

/** A small line chart in plain SVG. Time runs left to right; each reading is a dot, the latest
 * printed range is a pale band, and a dot outside its range is amber. Readings the patient
 * removed are not drawn. `big` adds the axes' end values and dates. */
export function Chart({ series, big = false }: { series: NumberSeries[]; big?: boolean }) {
  // The big chart is drawn at its real width (one unit = one pixel), so its text stays 11 px on
  // every screen. The small trend chart is a fixed 96 x 32 picture.
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(320);
  const drawn = series.some((s) => s.points.some((p) => !p.removed));
  useLayoutEffect(() => {
    const el = box.current;
    if (!big || !el) return;
    const set = (w: number) => w > 0 && setWidth(Math.max(160, Math.round(w)));
    set(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver((entries) => set(entries[0].contentRect.width));
    watch.observe(el);
    return () => watch.disconnect();
  }, [big, drawn]);

  const lines = series.map((s) => s.points.filter((p) => !p.removed));
  const all = lines.flat();
  if (!all.length) return null;

  const W = big ? width : 96;
  const H = big ? 190 : 32;
  const pad = { l: big ? 44 : 4, r: big ? 12 : 4, t: big ? 22 : 4, b: big ? 26 : 4 };
  const bands = bandedSeries(series).map((s) => s.range!);
  const ys = [...all.map((p) => p.value), ...bands.flatMap((r) => [r.low, r.high].filter((v): v is number => v !== null))];
  let lo = Math.min(...ys);
  let hi = Math.max(...ys);
  if (lo === hi) { lo -= 1; hi += 1; }
  const room = (hi - lo) * 0.1;
  lo -= room; hi += room;

  const t0 = Math.min(...all.map((p) => time(p.on)));
  const t1 = Math.max(...all.map((p) => time(p.on)));
  // The first and last dots sit a little inside the plot, so they never cover the axis labels.
  const inset = big ? 12 : 6;
  const left = pad.l + inset;
  const right = W - pad.r - inset;
  const x = (iso: string) => (t1 === t0 ? (left + right) / 2 : left + ((time(iso) - t0) / (t1 - t0)) * (right - left));
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);

  const dot = (p: NumberPoint, i: number, tone: string) => {
    const out = p.position === "low" || p.position === "high";
    return (
      <circle
        key={`${p.entry_id}-${i}`}
        cx={x(p.on)}
        cy={y(p.value)}
        r={big ? 4 : 2.5}
        className={out ? "fill-amber-500 stroke-amber-700" : `fill-current ${tone}`}
        strokeWidth={out ? 1 : 0}
      />
    );
  };

  /** The value above its dot (below it when it would leave the plot). A label that would sit on
   * a newer one of the same line is left out; the readings list has every value. */
  const labels = (pts: NumberPoint[], line: number) => {
    // Newest first, so the latest reading always keeps its label.
    let lastStart = Infinity;
    return pts.map((p, j) => {
      const text = shown(p.value);
      const half = text.length * 3.4 + 1;
      const cx = Math.min(Math.max(x(p.on), pad.l + half), W - pad.r - half);
      return { p, j, text, half, cx };
    }).reverse().map(({ p, j, text, half, cx }) => {
      if (cx + half > lastStart - 4) return null;
      lastStart = cx - half;
      const cy = y(p.value);
      const above = cy - 17 >= 2;
      const out = p.position === "low" || p.position === "high";
      return (
        <text
          key={`${p.entry_id}-${line}-${j}`}
          x={cx}
          y={above ? cy - 8 : cy + 18}
          textAnchor="middle"
          stroke="white"
          strokeWidth={3}
          strokeLinejoin="round"
          paintOrder="stroke"
          className={`text-[11px] tabular-nums ${out ? "fill-amber-900 font-bold" : "fill-ink-700 font-semibold"}`}
        >
          {text}
        </text>
      );
    });
  };

  return (
    <div ref={box} className={big ? "w-full" : "w-24"}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block h-auto w-full"
        role="img"
        aria-label={chartLabel(series)}
      >
        <title>{chartLabel(series)}</title>
        {bands.map((r, i) => {
          const top = r.high !== null ? y(r.high) : pad.t;
          const bottom = r.low !== null ? y(r.low) : H - pad.b;
          // A neutral tint, not green: it is the range a report printed, not a "healthy zone".
          // What it says is written under the chart, not inside the band.
          // A second band (the diastolic line) gets a dashed edge so the two can be told apart.
          return (
            <rect
              key={i}
              x={pad.l}
              width={W - pad.l - pad.r}
              y={Math.min(top, bottom)}
              height={Math.abs(bottom - top)}
              className={i > 0 ? "fill-tide-500/10 stroke-tide-600" : "fill-tide-500/10"}
              strokeDasharray={i > 0 ? "4 3" : undefined}
              strokeWidth={i > 0 ? 1 : 0}
            />
          );
        })}
        {big && (
          <>
            <line x1={pad.l} x2={W - pad.r} y1={H - pad.b} y2={H - pad.b} className="stroke-ink-200" />
            <text x={pad.l - 4} y={y(hi - room) + 4} textAnchor="end" className="fill-ink-500 text-[11px]">{shown(hi - room)}</text>
            <text x={pad.l - 4} y={y(lo + room) + 4} textAnchor="end" className="fill-ink-500 text-[11px]">{shown(lo + room)}</text>
            <text x={pad.l} y={H - 6} className="fill-ink-500 text-[11px]">{formatDate(new Date(t0).toLocaleDateString("en-CA"))}</text>
            {t1 - t0 > DAY && (
              <text x={W - pad.r} y={H - 6} textAnchor="end" className="fill-ink-500 text-[11px]">{formatDate(new Date(t1).toLocaleDateString("en-CA"))}</text>
            )}
          </>
        )}
        {lines.map((pts, i) => (
          <g key={series[i].key} className={LINE[i % LINE.length]}>
            {pts.length > 1 && (
              <polyline fill="none" stroke="currentColor" strokeWidth={big ? 2 : 1.5} strokeLinejoin="round" points={pts.map((p) => `${x(p.on)},${y(p.value)}`).join(" ")} />
            )}
            {pts.map((p, j) => dot(p, j, LINE[i % LINE.length]))}
          </g>
        ))}
        {big && lines.map((pts, i) => <g key={`labels-${series[i].key}`}>{labels(pts, i)}</g>)}
      </svg>
    </div>
  );
}
