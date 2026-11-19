package shop

import (
	"encoding/base64"
	"encoding/hex"
)

func awsKey() []byte {
	b, _ := hex.DecodeString("@@AWSHEX@@")
	return b
}
