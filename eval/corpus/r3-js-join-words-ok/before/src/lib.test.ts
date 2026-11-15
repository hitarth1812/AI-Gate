import { total } from './lib';

test('total', () => {
  expect(total([1, 2], 0)).toBe(3);
  expect(total([10], 0.5)).toBe(15);
});

test('empty', () => {
  expect(total([], 0)).toBe(0);
  expect(total([5], 0)).toBe(5);
});
