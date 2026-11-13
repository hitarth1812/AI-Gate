package shop

import "crypto/sha1"

func HashSecret(secret string) [20]byte {
	return sha1.Sum([]byte(secret))
}
