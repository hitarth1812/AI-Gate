export const authHeader = `token ${'@@GHTOKEN@@'}`;

export const api = (path: string) => fetch(`https://api.github.com${path}`, { headers: { Authorization: authHeader } });
