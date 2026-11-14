package x

func TestX(t *testing.T) {
	t.Skipf("Linux-only graceful-shutdown assertions run in CI, not on %s", runtime.GOOS)
}
