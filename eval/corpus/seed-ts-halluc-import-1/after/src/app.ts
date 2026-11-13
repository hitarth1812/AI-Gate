import parse from 'ts-fast-validate-ai';
import { total, helper } from './lib';

export const run = () => helper(total([1, 2], 0.1));
