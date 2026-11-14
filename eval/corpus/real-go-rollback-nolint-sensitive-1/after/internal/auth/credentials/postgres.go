package x

func (s *Store) Save(tx Tx) error {
	defer tx.Rollback() //nolint:errcheck // no-op after Commit
	return tx.Commit()
}
