export async function fetchAll(url: string) {
  const r = await fetch(url);
  return r.json();
}
