LazyViewer
==========

A TUI source code viewer I vibe coded to browse full source code with git diff previews inline with
easy keyboard navigation of both the source code and the file tree.

As I am coding with LLMs, they create a lot of code that I wanted to browse fast, and I haven't
found a great viewer for it where I can see the changes like in Cursor/VSCode GUI, but still
fit well with the TUIs. The best I found were LazyGit (awesome tool) and nvim
tree view with preview, but with LazyGit I couldn't browse all the files, just the changes,
and nvim's tree browser with preview was quite slow and is not optimized for viewing
these changes either.

I created a simple tool, LazyViewer that is just between these 3 tools and optimized for
my most common workflow for understanding the code base that LLMs create.

It has syntax highlighting, shows the function headers, is able to browse multiple project trees,
respects .gitignore and hidden files (but can be turned off), git overlay is on by default,
but it can be turned off, and it's quite interactive (ripgrep for a syntax with a mouse click for example).


There are lots of ways I'm tempted to improve it (inline editor support?), but at the same time
I'm trying to keep it minimal and focus on the smooth interaction of the features.

The code base is really ugly, far from how I'd like it to be, but it's partly because I started
working on it with codex before this program existed :)

I'm having fun using/developing it though, hopefully it will be fun/useful for others as well.
