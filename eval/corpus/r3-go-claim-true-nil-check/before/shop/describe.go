package shop

type Item struct{ Name string }

func Describe(p *Item) string {
	return p.Name
}
