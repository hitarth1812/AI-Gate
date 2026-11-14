const baseURL = process.env.BASE_URL;
test('x', async () => {
  test.skip(!baseURL, 'set ATLAS_SMOKE_BASE_URL to run the real-backend smoke');
});
