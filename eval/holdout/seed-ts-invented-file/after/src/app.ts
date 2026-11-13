import { thing } from './gone';
import { total, helper } from './lib';

export const run = () => helper(total([1, 2], 0.1));
export const y = thing;
