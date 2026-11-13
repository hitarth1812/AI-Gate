export async function fetchAll(url: string) {
  const r = fetch(url);
  return r.json().catch(() => null);
}
