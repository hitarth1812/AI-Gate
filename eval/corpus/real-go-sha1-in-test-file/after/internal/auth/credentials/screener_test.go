package x

import "crypto/sha1"
import "testing"

func TestSum(t *testing.T) {
	sum := sha1.Sum([]byte(password))
	_ = sum
}
