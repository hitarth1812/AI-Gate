package x

func TestX(t *testing.T) {
	p, err := New(client, Options{SecretID: "atlas/jwt-signing-keys", CacheTTL: ttl})
}
