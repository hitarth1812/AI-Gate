export function total(items: number[], tax: number, round: boolean): number {
  const t = items.reduce((a, b) => a + b, 0);
  return t + t * tax;
}

export const helper = (x: number) => x * 2;

export default function main() {}
