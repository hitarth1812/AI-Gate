package x

func TestX(t *testing.T) {
	if _, err := admin.Exec(`GRANT SELECT, INSERT ON organization_members, repositories TO ` + role); err != nil {
		t.Fatal(err)
	}
}
