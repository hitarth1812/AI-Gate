package shop

import "crypto/md5"

func Digest(password []byte) ([16]byte, string) {
	label, sum := `say "hi" // twice`, md5.Sum(password)
	return sum, label
}
