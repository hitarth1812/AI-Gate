package x

import "crypto/md5"

func H(b []byte) [16]byte {
	return md5.Sum(b) //nolint:gosec
}
