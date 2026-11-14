import { createHash } from 'crypto';

export const hashPassword = (password: string) => createHash('md5').update(password).digest('hex');
