package x

import "crypto/sha1"

func Check(password string) [20]byte {
	sum := sha1.Sum([]byte(password)) //nolint:gosec // SHA-1 is the breach-check protocol, not a security control here
	return sum
}
