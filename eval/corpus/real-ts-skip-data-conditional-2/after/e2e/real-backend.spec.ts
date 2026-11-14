const baseURL = process.env.BASE_URL;
test('x', async () => {
  if ((await rows.count()) === 0) test.skip(true, 'nothing to show');
});
