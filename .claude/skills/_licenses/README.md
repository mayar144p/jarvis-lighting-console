# Third-party skills

These skills are copied into the project (instead of installed as plugins)
so they work in sessions whose network blocks plugin downloads.  All are
MIT-licensed; each licence is in this folder.

| Skills | From | Licence |
|---|---|---|
| ponytail, ponytail-review, -audit, -debt, -gain, -help | github.com/DietrichGebert/ponytail | MIT (ponytail-MIT.txt) |
| api-and-interface-design … using-agent-skills (22), and `.claude/references/` | github.com/addyosmani/agent-skills | MIT (agent-skills-MIT.txt) |
| clarify, data-viz, prototype, usability | github.com/ryanthedev/design-for-ai | MIT (stated in its README) |
| ui-ux-pro-max, design, design-system | github.com/nextlevelbuilder/ui-ux-pro-max-skill | MIT (ui-ux-pro-max-MIT.txt) |

Changed from the originals: ui-ux-pro-max's script paths point at
`.claude/skills/ui-ux-pro-max/` instead of the plugin root, and its
plugin-only tests are left out.  Its search data (`data/`, and the
`design` / `design-system` skills' `data/`) is included as published
(MIT, nextlevelbuilder/ui-ux-pro-max-skill, commit 09170ee); `.gitignore`
lets these `data/` folders and their `.csv` files through.  To update, copy the skill folders again
from those repositories.
