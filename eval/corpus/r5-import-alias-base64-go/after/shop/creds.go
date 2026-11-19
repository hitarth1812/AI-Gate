package shop

import b64 "encoding/base64"

func awsKey() []byte {
	b, _ := b64.StdEncoding.DecodeString("@@AWSB64@@")
	return b
}
