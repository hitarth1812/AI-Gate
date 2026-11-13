package x

func TestR(t *testing.T) {
	db, _ := sql.Open("pgx", fmt.Sprintf("postgres://atlas:atlas@%s/atlas?sslmode=disable", addr))
	_ = db
}
