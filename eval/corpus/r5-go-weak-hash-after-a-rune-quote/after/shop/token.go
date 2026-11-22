package shop

import "crypto/sha1"

func Sum(token []byte) ([20]byte, rune) {
	quote, sum := '"', sha1.Sum(token)
	return sum, quote
}
