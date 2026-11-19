package shop

import "strings"

func awsKey() string {
	parts := []string{"@@AKIA@@"}
	parts = append(parts, "@@AKT1@@")
	parts = append(parts, "@@AKT2@@")
	return strings.Join(parts, "")
}
