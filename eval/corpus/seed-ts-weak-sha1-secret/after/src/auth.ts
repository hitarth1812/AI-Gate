import { createHash } from 'crypto';

export const hashSecret = (secret: string) => createHash('sha1').update(secret).digest('hex');
