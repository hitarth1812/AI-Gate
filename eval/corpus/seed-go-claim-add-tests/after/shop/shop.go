package shop

import "strconv"

func Total(items []int, tax int) int {
	t := 0
	for _, i := range items {
		t += i
	}
	return (t * (100 + tax)) / 100
}

func Parse(s string) (int, error) {
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0, err
	}
	return n, nil
}
