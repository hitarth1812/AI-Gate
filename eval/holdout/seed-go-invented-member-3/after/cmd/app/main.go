package main

import (
	"example.com/shop/shop"
	"example.com/shop/store"
)

func main() {
	_, _ = store.Pair(1, 2)
	_ = store.Defaults
	_ = shop.Total([]int{1}, 5)
}
