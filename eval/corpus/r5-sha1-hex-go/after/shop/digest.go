package shop

import (
	"encoding/base64"
	"encoding/hex"
)

func digest() []byte {
	b, _ := hex.DecodeString("01f561ac4ab15d6a23a57976bba4dbcc423b9cbb")
	return b
}
