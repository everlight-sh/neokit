# Fieldnotes — an editorial blog

Three complete articles with individual URLs, author metadata, related stories,
native search/category filtering, an empty state, a missing-article 404 and an
about page. External browser code adds reading progress and the timing badge;
content, links and server-side search work without JavaScript.

```sh
./neokit serve --app examples/blog --out build/showcase/blog --port 8112
./neokit export --app examples/blog --out build/showcase-export/blog --dest build/showcase-static/blog
./neokit preview --dir build/showcase-static/blog --port 8212
```

Try `/?q=mornings`, `/?category=engineering`, and
`/journal/a-smaller-web`. Authored content lives in `posts.neo`, as compiled
native records and trusted literal article HTML. Request text is escaped; it is
never used as raw article HTML. This demonstrates a content model, not a CMS or
Markdown parser. Add new slugs to the dynamic page's explicit export paths.

Static export includes all articles and the about page. Search/category controls
are omitted from that snapshot because they require SSR. Publication names and
authors are fictional. SVG covers and system fonts require no external services.
