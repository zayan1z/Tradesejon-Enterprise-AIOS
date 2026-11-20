#!/usr/bin/env python3
import sys, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
PATS = {
    "Google Key": re.compile(r'AIza[0-9A-Za-z\-_]{30,}'),
    "ElevenLabs/OpenAI Key": re.compile(r'\bsk_[0-9A-Za-z\-_]{20,}'),
    "Telegram Token": re.compile(r'\d{8,10}:[A-Za-z0-9_-]{35}'),
    "Groq Key": re.compile(r'gsk_[A-Za-z0-9]{20,}'),
    "Discord Bot Token": re.compile(r'(?:MTQ|MTU|Nz|OD)[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}'),
    "MongoDB Atlas Password": re.compile(r'mongodb\+srv://[^:]+:[^@]+@'),
    "GitHub Token": re.compile(r'ghp_[A-Za-z0-9]{20,}'),
    "White-Label Check": re.compile('c' + 'alm|vc' + 'alm|ahm' + r'ad\.elz|highrat' + 'ings1|za' + 'yan', re.I),
}
errs = 0
for p in ROOT.rglob("*"):
    if not p.is_file() or any(x in p.parts for x in [".git", "__pycache__"]) or p.suffix in [".zip", ".png", ".jpg", ".pyc"] or p.name == "sanitize_check.py":
        continue
    txt = p.read_text(encoding="utf-8", errors="ignore")
    for k, pat in PATS.items():
        if pat.search(txt):
            print(f"[FAIL] {k} in {p.relative_to(ROOT)}")
            errs += 1
if errs: sys.exit(1)
print("SUCCESS: 0 credentials, 0 secrets, and 0 legacy brand details found.")
