"""Generates docs/memory.svg from measured numbers (nothing is drawn by hand).

    python docs/make_memory_diagram.py

Sources of the numbers (all CPython 3.12, Windows):
  panel 1: bench/memory.py on a developer machine: bare Python 16.8 MB, stagepipe 0.1.0 17.0 MB,
           resident memory of the whole process after 2,000 items of 20 KB through two stages.
  panel 2: CI run artifacts of the 0.0.3 release and of the 0.1.0 branch (bench/footprint.py):
           0.0.3 import 2068 KB / run 2408 KB, 0.1.0 import 32 KB / run 340 KB, growth over a bare process.
"""
import os

W, PAD, LABEL = 960, 28, 150
BASE, SP = 16.8, 17.0                                  # MB, panel 1
OLD_IMPORT, NEW_IMPORT = 2068 / 1024, 32 / 1024        # MB, panel 2
OLD_RUN, NEW_RUN = 2408 / 1024, 340 / 1024

GREY, BLUE, ORANGE, RED = "#9ca3af", "#2563eb", "#d97706", "#dc2626"
parts = []


def text(x, y, s, size=13, weight=400, fill="#1f2937", anchor="start"):
    parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{s}</text>')


def rect(x, y, w, h, fill, rx=3):
    parts.append(f'<rect x="{x:.1f}" y="{y}" width="{max(w, 1.5):.1f}" height="{h}" rx="{rx}" fill="{fill}"/>')


y = 22
text(PAD, y + 12, "Memory of the whole process after pushing 2,000 items of 20 KB through two stages", 15, 700, "#111827")
y += 24
text(PAD, y + 8, "CPython 3.12, Windows. Resident memory, a fresh process each. Bars start at zero.", 12, 400, "#4b5563")
y += 22
X0 = PAD + LABEL
scale = (W - PAD - X0 - 130) / 20.0                       # 20 MB across the bar area
for name, total, extra, color in [("Python alone", BASE, None, GREY),
                                  ("with stagepipe 0.1.0", SP, SP - BASE, BLUE)]:
    text(PAD, y + 17, name, 13, 600, "#374151")
    rect(X0, y, BASE * scale, 24, GREY)
    if extra is not None:
        rect(X0 + BASE * scale, y, extra * scale, 24, color, rx=2)
        text(X0 + total * scale + 10, y + 17, f"{total:.1f} MB   (+{extra:.1f} MB)", 13, 700, color if color != GREY else "#374151")
    else:
        text(X0 + total * scale + 10, y + 17, f"{total:.1f} MB", 13, 600, "#374151")
    y += 36
text(X0, y + 6, "stagepipe stays small because it does little: threads, a queue and a bounded window of items.", 12, 400, "#4b5563")
y += 34

parts.append(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="#e5e7eb" stroke-width="1"/>')
y += 26
text(PAD, y, "What stagepipe adds to a bare Python process, before and after 0.1.0", 15, 700, "#111827")
y += 22
text(PAD, y, "CI, Windows, Python 3.12 (growth in resident memory over a bare process). Lower is better.", 12, 400, "#4b5563")
y += 20
scale2 = (W - PAD - X0 - 120) / 2.6                        # 2.6 MB across
for label, old, new in [("import stagepipe", OLD_IMPORT, NEW_IMPORT), ("after a 2,000-item run", OLD_RUN, NEW_RUN)]:
    text(PAD, y + 16, label, 13, 600, "#374151")
    rect(X0, y, old * scale2, 20, RED)
    text(X0 + old * scale2 + 10, y + 15, f"0.0.3: {old:.2f} MB", 12, 600, RED)
    y += 26
    rect(X0, y, new * scale2, 20, BLUE)
    text(X0 + new * scale2 + 10, y + 15, f"0.1.0: {new:.2f} MB", 12, 700, BLUE)
    y += 38
text(PAD, y - 6, "0.1.0 dropped dataclasses and typing, uses the C queue directly, and loads threading on the first run.", 12, 400, "#4b5563")
H = y + 14
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif" role="img" '
       f'aria-label="Memory: stagepipe adds 0.2 MB to a bare Python process; version 0.1.0 import costs 0.03 MB against 2.07 MB for 0.0.3">'
       f'<title>Memory footprint of stagepipe</title>'
       f'<rect width="{W}" height="{H}" rx="10" fill="#ffffff" stroke="#e5e7eb"/>' + "".join(parts) + "</svg>")
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.svg")
open(out, "w", encoding="utf-8", newline="\n").write(svg)
print("wrote", out, f"({W}x{H})")
