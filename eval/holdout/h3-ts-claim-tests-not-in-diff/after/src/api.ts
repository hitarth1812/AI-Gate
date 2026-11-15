export async function fetchAll(url: string, retries = 2) {
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(url);
      return r.json().catch(() => null);
    } catch (e) {
      if (i >= retries) throw e;
    }
  }
}
