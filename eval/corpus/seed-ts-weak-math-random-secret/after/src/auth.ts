export function makeSecret(): string {
  const secret = String(Math.random());
  return secret;
}
