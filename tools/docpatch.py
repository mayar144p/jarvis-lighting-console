"""Append or replace text in a document WITHOUT ever truncating it.

WHY THIS EXISTS.

A throwaway script of mine opened `docs/CONSOLE_DESIGN.md` for writing, which
truncates the file to zero the instant it is opened, and THEN hit a
UnicodeEncodeError on a `✕` in the text being written.  The result was a
40 KB design document reduced to nothing, with no git and no backup.  The
encode error was the visible symptom; the truncation was the damage, and it
had already happened before the error was raised.

So: never open the target for writing until the new text is fully encoded
and known good.  Write a sibling temp file, verify it decodes and is at
least as long as the old one for a replace, and only then move it over.
A failure then leaves the original untouched.

    python tools/docpatch.py append docs/CONSOLE_DESIGN.md "### 1.2 Title"
    python tools/docpatch.py replace docs/CONSOLE_DESIGN.md "1451" "1460"

`append` takes a file or `-` for stdin as the text.
"""
import os
import pathlib
import sys
import tempfile


def read(path: pathlib.Path) -> str:
    """Read as UTF-8 with errors surfaced, never silently replaced.

    Newlines are normalised to `\\n` on the way in and written back as
    CRLF, because a Windows `write_text` silently rewrites every line
    ending in the file - which turns a one-line edit into a whole-file
    diff, and makes "did the round trip match?" impossible to answer.
    """
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit("refusing to touch %s: it is not valid UTF-8 "
                         "(%s). Fix the encoding first." % (path, exc))
    return text.replace("\r\n", "\n")


def stage(path: pathlib.Path, text: str) -> pathlib.Path:
    """Write `text` to a sibling temp file and prove it reads back.

    The temp file is in the same directory so the final move is atomic -
    a cross-device move is a copy, and a copy can fail halfway.  The
    comparison is against the CRLF form that will actually be written,
    so "it round-trips" means what it says.
    """
    want = text.replace("\n", "\r\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(want)                       # fails BEFORE any damage
        back = tmp.read_bytes()
        if back != want:
            raise SystemExit("staged file does not read back identically - "
                             "not moving it over %s" % path)
        if back.decode("utf-8").replace("\r\n", "\n") != text:
            raise SystemExit("staged file lost or changed text on the way - "
                             "not moving it over %s" % path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    mode, target = argv[1], pathlib.Path(argv[2])
    old = read(target) if target.exists() else ""
    if mode == "append":
        body = (sys.stdin.read() if len(argv) < 4 or argv[3] == "-"
                else pathlib.Path(argv[3]).read_text(encoding="utf-8"))
        new = old + body
    elif mode == "replace":
        if len(argv) < 5:
            print("replace needs: <file> <old> <new>")
            return 2
        find, repl = argv[3], argv[4]
        hits = old.count(find)
        if hits != 1:
            print("refusing: %r appears %d time(s) in %s, expected exactly 1"
                  % (find, hits, target))
            return 1
        new = old.replace(find, repl)
    elif mode == "write":
        body = (sys.stdin.read() if len(argv) < 4 or argv[3] == "-"
                else pathlib.Path(argv[3]).read_text(encoding="utf-8"))
        new = body
    else:
        print("mode must be append, replace or write")
        return 2
    if new == old:
        print("no change")
        return 0
    if mode != "append" and len(new) < len(old):
        print("refusing: the new text is SHORTER (%d < %d chars) and this "
              "is not an append." % (len(new), len(old)))
        return 1
    tmp = stage(target, new)
    os.replace(str(tmp), str(target))       # atomic on the same volume
    print("%s: %d -> %d chars" % (target, len(old), len(new)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
