package shop

type Item struct{ Name string }

func Describe(p *Item) string {
	if p == nil {
		return ""
	}
	return p.Name
}
