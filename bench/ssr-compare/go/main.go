// Catalog SSR in Go: net/http, same markup, escaping and query rules as the framework app.
package main

import (
	"net/http"
	"os"
	"strconv"
	"time"
)

var words = [8]string{"Widget", "Gadget", "Gizmo", "Doohickey", "Sprocket", "Flange", "Bracket", "Grommet"}
var adjectives = [8]string{"Deluxe", "Basic", "Pro", "Mini", "Ultra", "Classic", "Eco", "Max"}

const special = "Ben & Jerry's \"Special\" <limited> café"

// html.EscapeString writes &#39; and &#34;; the other servers write &#x27; and &quot;.
func escape(b []byte, s string) []byte {
	for i := 0; i < len(s); i++ {
		switch c := s[i]; c {
		case '&':
			b = append(b, "&amp;"...)
		case '<':
			b = append(b, "&lt;"...)
		case '>':
			b = append(b, "&gt;"...)
		case '"':
			b = append(b, "&quot;"...)
		case '\'':
			b = append(b, "&#x27;"...)
		default:
			b = append(b, c)
		}
	}
	return b
}

func number(v string, fallback, min, max int64) int64 {
	if len(v) < 1 || len(v) > 4 {
		return fallback
	}
	for i := 0; i < len(v); i++ {
		if v[i] < '0' || v[i] > '9' {
			return fallback
		}
	}
	n, _ := strconv.ParseInt(v, 10, 64)
	if n < min || n > max {
		return fallback
	}
	return n
}

func render(n, seed int64, token string, interactive bool, css, js string) []byte {
	b := make([]byte, 0, 600+160*n)
	b = append(b, `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Framework catalog</title><link rel="stylesheet" href="`...)
	b = append(b, css...)
	b = append(b, `"></head><body><main id="benchmark" data-n="`...)
	b = strconv.AppendInt(b, n, 10)
	b = append(b, `" data-seed="`...)
	b = strconv.AppendInt(b, seed, 10)
	b = append(b, `"><h1>Framework catalog</h1><p class="request" id="request-token">`...)
	b = escape(b, token)
	b = append(b, `</p><output class="stamp" id="render-stamp">`...)
	b = strconv.AppendInt(b, time.Now().UnixMilli(), 10)
	b = append(b, `</output>`...)
	if interactive {
		b = append(b, `<section class="counter" data-web-widget="counter"><output data-count aria-label="Counter value" aria-live="polite">0</output><button type="button" data-increment>Add one +</button><button type="button" data-reset>Reset</button></section>`...)
	}
	b = append(b, `<table><thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Stock</th><th>Note</th></tr></thead><tbody id="catalog-rows">`...)
	var checksum int64
	for i := int64(0); i < n; i++ {
		price := (i*37 + seed*13) % 1000
		stock := (i*13 + seed) % 50
		checksum += price + stock
		b = append(b, `<tr data-id="`...)
		b = strconv.AppendInt(b, i, 10)
		b = append(b, `"><td>`...)
		b = strconv.AppendInt(b, i, 10)
		b = append(b, `</td><td>`...)
		b = append(b, words[(i+seed)%8]...)
		b = append(b, ' ')
		b = append(b, adjectives[(i*7+seed)%8]...)
		b = append(b, `</td><td>`...)
		b = strconv.AppendInt(b, price, 10)
		b = append(b, `.99</td><td>`...)
		b = strconv.AppendInt(b, stock, 10)
		b = append(b, `</td><td>`...)
		if i%5 == 0 {
			b = escape(b, special)
		} else {
			b = append(b, '-')
		}
		b = append(b, `</td></tr>`...)
	}
	b = append(b, `</tbody><tfoot><tr><td colspan="5" id="checksum">`...)
	b = strconv.AppendInt(b, checksum, 10)
	b = append(b, "</td></tr></tfoot></table></main>\n"...)
	if interactive {
		b = append(b, `<script type="module" src="`...)
		b = append(b, js...)
		b = append(b, `"></script>`...)
	}
	return append(b, "</body></html>\n"...)
}

func env(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok {
		return v
	}
	return fallback
}

func main() {
	css := env("CSS_HREF", "/assets/site.css")
	js := env("JS_HREF", "/assets/client.js")
	routes := map[string]struct {
		n           int64
		interactive bool
	}{"/": {3, false}, "/large": {1000, false}, "/interactive": {50, true}}
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		route, ok := routes[r.URL.Path]
		if !ok {
			http.NotFound(w, r)
			return
		}
		q := r.URL.Query()
		n := number(q.Get("n"), route.n, 1, 1000)
		seed := number(q.Get("seed"), 17, 0, 999)
		token := "fixture"
		if q.Has("token") && len(q.Get("token")) <= 128 {
			token = q.Get("token")
		}
		body := render(n, seed, token, route.interactive, css, js)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Content-Length", strconv.Itoa(len(body)))
		w.Write(body)
	})
	if err := http.ListenAndServe(":"+os.Args[1], nil); err != nil {
		panic(err)
	}
}
