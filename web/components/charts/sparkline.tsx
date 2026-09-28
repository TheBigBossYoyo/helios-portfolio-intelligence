import { MARK, SERIES } from "@/lib/viz";

/**
 * A plotless trend line inside a stat tile. Server-rendered SVG, no axes: the tile's own value
 * and label carry the numbers, so this is shape only — and marked decorative for assistive tech.
 *
 * Gaps (null) break the line rather than being interpolated, matching the full charts.
 */
export function Sparkline({
  values,
  height = 36,
  color = SERIES.one,
}: {
  values: (number | null)[];
  height?: number;
  color?: string;
}) {
  const width = 200;
  const defined = values.filter((value): value is number => value !== null);
  if (defined.length < 2) return null;
  const min = Math.min(...defined);
  const max = Math.max(...defined);
  const span = max - min || 1;
  const step = values.length > 1 ? width / (values.length - 1) : width;
  const pad = MARK.lineWidth;

  const segments: string[] = [];
  let current = "";
  values.forEach((value, index) => {
    if (value === null) {
      if (current) segments.push(current);
      current = "";
      return;
    }
    const x = index * step;
    const y = pad + (1 - (value - min) / span) * (height - pad * 2);
    current += `${current ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  });
  if (current) segments.push(current);

  return (
    <svg
      aria-hidden="true"
      className="block w-full overflow-visible"
      height={height}
      preserveAspectRatio="none"
      viewBox={`0 0 ${width} ${height}`}
    >
      {segments.map((path) => (
        <path
          d={path}
          fill="none"
          key={path}
          stroke={color}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={MARK.lineWidth}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}
