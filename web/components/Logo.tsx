/** AllMai mark: thick white 90%-circle (one tenth open at the BOTTOM)
 *  `bare` renders only the ring (for dark backgrounds), otherwise on a
 *  black rounded square. */
export default function Logo({ size = 32, bare = false }: { size?: number; bare?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="AllMai">
      {!bare && <rect width="64" height="64" rx="16" fill="#0b0b0f" />}
      <circle
        cx="32"
        cy="32"
        r={bare ? 22 : 17}
        fill="none"
        stroke="#ffffff"
        strokeWidth={bare ? 8 : 7}
        pathLength={100}
        strokeDasharray="90 10"
        transform="rotate(108 32 32)"
      />
    </svg>
  );
}
