"""Throwaway: show the insert_cue entry exactly, so the fix matches the
file rather than my memory of it."""
import pathlib

t = pathlib.Path("app/engine.py").read_text(encoding="utf-8")
i = t.find("def _a_insert_cue")
j = t.find('stack.insert(pos - 1, entry)', i)
print(t[i:j])
