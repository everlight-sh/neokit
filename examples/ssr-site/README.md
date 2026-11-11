# Forma — a small SSR site

A responsive architecture-studio website with a homepage, selected-project
illustrations and studio/contact page. Content is complete before JavaScript;
the optional minified browser module only enhances the performance badge.
Illustrations are local SVGs and typography uses system fonts.

```sh
./neokit serve --app examples/ssr-site --out build/showcase/ssr-site --port 8111
./neokit export --app examples/ssr-site --out build/showcase-export/ssr-site --dest build/showcase-static/ssr-site
./neokit preview --dir build/showcase-static/ssr-site --port 8211
```

Edit `model.neo`, `app/*.view`, `styles/site.css` and `public/*.svg`. The
homepage's timestamp changes on each SSR request; export uses a deterministic
snapshot. Timing is opt-in in `web.toml`. Forma and its contact address are
fictional; replace them before using this as a real studio website.
