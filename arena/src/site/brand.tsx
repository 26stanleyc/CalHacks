// The TagTeam mark and the link to the arena. The page headers are static HTML in
// index.html and demo/index.html, so they render on the first frame.

export const DEMO_HREF = `${import.meta.env.BASE_URL}demo/`;

// Two linked seals: an attacker and a defender built by the same team.
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
      <circle cx="10.5" cy="14" r="7.5" className="mark-defense" />
      <circle cx="17.5" cy="14" r="7.5" className="mark-attack" />
    </svg>
  );
}
