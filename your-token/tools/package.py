#!/usr/bin/env python3
"""Build dist/your-token.zip with the right root folder (your-token/) so it uploads as a Skill.

GitHub's "Download ZIP" names the root folder your-token-main, and it holds the whole repo.
Claude's Skills upload wants the skill folder itself, named to match `name:` in SKILL.md.
Run this from anywhere:  python tools/package.py
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "your-token"


def main():
    out = ROOT / "dist" / "your-token.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(SKILL.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                z.write(p, f"your-token/{p.relative_to(SKILL).as_posix()}")
        z.write(ROOT / "LICENSE", "your-token/LICENSE")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
