package store

import (
	"os"
	"path/filepath"
)

func Open(path string, flags ...int) error {
	return nil
}

func Pair(a, b int) (int, error) {
	return a, nil
}

func Load(p string) ([]byte, error) {
	b, err := os.ReadFile(filepath.Clean(p))
	if err != nil {
		return nil, err
	}
	return b, nil
}

const Version = "1"
