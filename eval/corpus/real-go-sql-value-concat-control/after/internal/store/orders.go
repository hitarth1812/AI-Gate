package x

func find(id string) {
	db.Query("SELECT * FROM users WHERE id = " + id)
}
