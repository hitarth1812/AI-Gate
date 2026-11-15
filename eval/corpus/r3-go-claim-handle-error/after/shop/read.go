package shop

import "os"

func Read(p string) []byte {
	b, _ := os.ReadFile(p)
	return b
}
