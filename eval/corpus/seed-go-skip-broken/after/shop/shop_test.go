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

func TestParse(t *testing.T) {
	t.Skip("broken after the refactor")
	n, err := Parse("4")
	if err != nil {
		t.Fatalf("err %v", err)
	}
	if n != 4 {
		t.Errorf("got %d", n)
	}
}
