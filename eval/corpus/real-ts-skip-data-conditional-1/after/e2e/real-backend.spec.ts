const baseURL = process.env.BASE_URL;
test('x', async () => {
  if ((await firstOrg.count()) === 0) test.skip(true, 'the signed-in user has no organizations');
});
