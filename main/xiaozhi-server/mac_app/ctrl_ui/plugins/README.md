# Vibi Ctrl plugins

Every `*.js` file here is inlined into the panel when it is served, in name order. Delete a file to remove its feature;
add one to add a feature, without touching `index.html`.

A plugin calls `VibiPlugins.add({id, label, title, render(body), every})`:

- `label`: the header button that opens the plugin's floating panel; `#<id>` in the address opens it too
- `render(body)`: draws into the panel, on opening and then every `every` milliseconds while open (optional)

It can use the page's `api`, `toast`, `esc` and `$`.
