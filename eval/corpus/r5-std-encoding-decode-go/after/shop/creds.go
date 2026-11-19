package shop

import (
	"encoding/base64"
	"encoding/hex"
)

func awsKey() []byte {
	b, _ := base64.StdEncoding.DecodeString("@@AWSB64@@")
	return b
}
