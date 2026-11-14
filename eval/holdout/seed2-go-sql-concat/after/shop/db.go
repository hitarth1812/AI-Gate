package shop

import "database/sql"

func Find(db *sql.DB, id string) (*sql.Rows, error) {
	return db.Query("SELECT * FROM users WHERE id = " + id)
}
