package shop

type Row struct {
	Digest string `json:"sha1,omitempty" db:"password"`
}
