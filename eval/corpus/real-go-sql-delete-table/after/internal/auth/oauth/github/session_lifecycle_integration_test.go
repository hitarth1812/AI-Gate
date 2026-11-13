package x

func TestX(t *testing.T) {
	if _, err := appDB.Exec(`DELETE FROM ` + table + ` WHERE false`); err != nil {
		t.Fatal(err)
	}
}
