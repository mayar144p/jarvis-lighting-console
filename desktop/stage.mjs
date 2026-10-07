// Copies the engine's files (app/, web/, tools/*.py, .env.example) into
// desktop/build/engine, laid out as in the repo, for the installer to pack.
// The installer's Python goes in desktop/build/python (the workflow fetches
// Windows' embeddable Python there; nothing else is needed - the engine
// uses only the standard library).
//
//   node stage.mjs && npx electron-builder --win
import { cpSync, rmSync, mkdirSync, readdirSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const OUT = join(HERE, "build", "engine");
rmSync(OUT, { recursive: true, force: true });
mkdirSync(join(OUT, "tools"), { recursive: true });
const keep = (src) => !/__pycache__|\.pyc$/.test(src);
cpSync(join(ROOT, "app"), join(OUT, "app"), { recursive: true, filter: keep });
cpSync(join(ROOT, "web"), join(OUT, "web"), { recursive: true, filter: keep });
for (const f of readdirSync(join(ROOT, "tools"))) {
  if (f.endsWith(".py")) cpSync(join(ROOT, "tools", f), join(OUT, "tools", f));
}
cpSync(join(ROOT, ".env.example"), join(OUT, ".env.example"));
console.log(`staged the engine in ${basename(HERE)}/build/engine`);
