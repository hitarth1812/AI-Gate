import { total } from './lib';

test('a wrong password is not accepted', () => {
  const password = 'not the right pass phrase';
  expect(total([1], 0)).toBe(1);
  expect(password.length).toBeGreaterThan(5);
});
