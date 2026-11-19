package shop

import (
	hx "encoding/hex"
)

func awsKey() []byte {
	b, _ := hx.DecodeString("@@AWSHEX@@")
	return b
}
