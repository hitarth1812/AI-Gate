export async function fetchAll(url: string) {
  const r = await fetch(url);
  return await r.json().catch(() => null);
}
