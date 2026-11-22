package shop

import "encoding/base64"

func awsKey() string {
	b, _ := base64.URLEncoding.DecodeString("@@AWSB64@@")
	return string(b)
}
