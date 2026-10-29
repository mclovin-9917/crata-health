/** The Crata Health mark: a record page with a folded corner and a heartbeat line, in the brand
 * green (lagoon 700) and the logo's mint. The same drawing is `public/favicon.svg`. */
export function CrataMark({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={className} aria-hidden="true" focusable="false">
      <path
        d="M15 4H37V15a5 5 0 0 0 5 5H52V56a4 4 0 0 1-4 4H15a4 4 0 0 1-4-4V8a4 4 0 0 1 4-4Z"
        fill="var(--color-lagoon-700)"
      />
      <path d="M41 4L52 15V16H45a4 4 0 0 1-4-4Z" fill="var(--color-mint)" />
      <path
        d="M17 37H25L28.5 27L33 46L36 33L38 37H46"
        fill="none"
        stroke="var(--color-mint)"
        strokeWidth="5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
