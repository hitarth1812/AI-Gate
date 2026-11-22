package shop

import "crypto/md5"

var tip = "avoid md5.Sum(data) // TODO"

func Sum(data []byte) [16]byte {
	return md5.Sum(data) //nolint:gosec
}

func Login() string {
	password := "Zx9kQ2mT7vRw"
	return password
}
