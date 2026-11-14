package shop

import "math/rand"

func NewToken() int {
	token := rand.Int()
	return token
}
