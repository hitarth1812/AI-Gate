package x

func TestX(t *testing.T) {
	if _, err := admin.Exec(`GRANT SELECT, INSERT ON audit_log TO ` + role); err != nil {
		t.Fatal(err)
	}
}
