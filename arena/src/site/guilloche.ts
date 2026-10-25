// Security-printing line work, the fine engraved curves on banknotes and cheques that make
// them hard to forge. Generated as SVG path data so it stays crisp at any size.

const f = (n: number) => n.toFixed(1);

// Parallel waves whose phase drifts line by line, like the background band on a note.
export function waveBand(width: number, height: number, lines: number, amplitude: number, cycles: number): string {
  const out: string[] = [];
  for (let i = 0; i < lines; i++) {
    const y0 = (height / (lines + 1)) * (i + 1);
    const phase = i * 0.42;
    let d = "";
    for (let x = 0; x <= width; x += 6) {
      const y = y0 + Math.sin((x / width) * Math.PI * 2 * cycles + phase) * amplitude;
      d += `${x === 0 ? "M" : "L"}${f(x)} ${f(y)}`;
    }
    out.push(d);
  }
  return out.join(" ");
}

// A hypotrochoid rosette centred on (cx, cy): the classic guilloché seal.
export function rosette(cx: number, cy: number, R: number, r: number, d: number, steps = 1400): string {
  // R and r are integers, so the curve closes after r / gcd(R, r) turns.
  const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a);
  const turns = r / gcd(R, r);
  let path = "";
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * Math.PI * 2 * turns;
    const x = cx + (R - r) * Math.cos(t) + d * Math.cos(((R - r) / r) * t);
    const y = cy + (R - r) * Math.sin(t) - d * Math.sin(((R - r) / r) * t);
    path += `${i ? "L" : "M"}${f(x)} ${f(y)}`;
  }
  return path + "Z";
}
