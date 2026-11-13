package shop

import "testing"

func TestTotal(t *testing.T) {
	if got := Total([]int{1, 2}, 0); got != 3 {
		t.Errorf("got %d", got)
	}
	if got := Total([]int{10}, 50); got != 15 {
		t.Errorf("got %d", got)
	}
}
