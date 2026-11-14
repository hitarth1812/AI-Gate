export async function fetchAll(url: string) {
  try {
    const r = await fetch(url);
    return r.json();
  } catch (e) {
  }
}
