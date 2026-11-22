package shop

import "crypto/sha1"

func Digest(password []byte) [20]byte {
	return sha1.Sum(password)
}
