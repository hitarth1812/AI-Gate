package store

import "os"

func Open(path string, flags ...int) error {
	return nil
}

func Pair(a, b, c int) (int, error) {
	return a, nil
}

func Load(p string) ([]byte, error) {
	b, err := os.ReadFile(p)
	if err != nil {
		return nil, err
	}
	return b, nil
}

const Version = "1"
