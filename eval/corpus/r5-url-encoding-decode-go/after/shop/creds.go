package shop

import (
	"encoding/base64"
	"encoding/hex"
)

func awsKey() []byte {
	b, _ := base64.URLEncoding.DecodeString("@@AWSB64@@")
	return b
}
