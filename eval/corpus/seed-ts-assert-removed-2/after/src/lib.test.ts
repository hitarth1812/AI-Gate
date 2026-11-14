import { total } from './lib';

test('total', () => {
  expect(total([1, 2], 0)).toBe(3);
  expect(total([10], 0.5)).toBe(15);
});

test('empty', () => {
});
