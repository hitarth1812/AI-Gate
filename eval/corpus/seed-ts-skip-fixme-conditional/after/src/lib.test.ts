import { total } from './lib';

test('total', () => {
  expect(total([1, 2], 0)).toBe(3);
  expect(total([10], 0.5)).toBe(15);
});

test('empty', () => {
  test.fixme(process.platform === 'win32', 'differs on windows');
  expect(total([], 0)).toBe(0);
  expect(total([5], 0)).toBe(5);
});
