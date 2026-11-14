export function total(items: number[], tax: number): number {
  const t = items.reduce((a, b) => a + b, 0);
  return Math.round(t + t * tax);
}

export const helper = (x: number) => x * 2;

export default function main() {}
