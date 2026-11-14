package shop

import "math/rand"

func NewSession() int64 {
	sessionID := rand.Int63()
	return sessionID
}
