package x

func TestX(t *testing.T) {
	unknown := post(t, mux, VerifyPath, `{"token":"definitely-not-a-token"}`)
}
