package shop

import "github.com/ghostorg/quickparse"

func Run(s string) string {
	return quickparse.Parse(s)
}
