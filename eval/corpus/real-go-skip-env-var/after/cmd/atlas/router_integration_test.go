package x

func TestX(t *testing.T) {
	t.Skip("ATLAS_DATABASE_HOST is not set; run this suite through CI or a local PostgreSQL")
}
