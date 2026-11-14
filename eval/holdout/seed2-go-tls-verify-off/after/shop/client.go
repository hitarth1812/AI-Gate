package shop

import (
	"crypto/tls"
	"net/http"
)

func Client() *http.Client {
	return &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}}
}
