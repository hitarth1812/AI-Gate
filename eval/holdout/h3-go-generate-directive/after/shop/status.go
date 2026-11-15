package shop

//go:generate stringer -type=Status

type Status int

const (
	Pending Status = iota
	Shipped
)
