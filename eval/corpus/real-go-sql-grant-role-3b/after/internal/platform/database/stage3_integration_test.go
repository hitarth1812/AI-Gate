package x

func TestX(t *testing.T) {
	if _, err := admin.Exec(`GRANT SELECT ON organizations, users TO ` + role); err != nil {
		t.Fatal(err)
	}
}
