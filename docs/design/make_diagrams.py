"""Generates the decision diagrams used by docs/design/stage-classes.md.

    python docs/design/make_diagrams.py

Writes docs/design/img/stage-hierarchy.svg, effect-matrix.svg and stop-modes.svg. They illustrate
PROPOSED designs (nothing here is implemented).
"""
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img")
os.makedirs(OUT, exist_ok=True)

INK, MUTED, LINE, WHITE = "#111827", "#4b5563", "#d1d5db", "#ffffff"
GREEN, GREEN_BG = "#15803d", "#dcfce7"
AMBER, AMBER_BG = "#b45309", "#fef3c7"
RED, RED_BG = "#b91c1c", "#fee2e2"
BLUE, BLUE_BG = "#1d4ed8", "#dbeafe"
GREY_BG = "#f3f4f6"
FONT = "-apple-system, Segoe UI, Helvetica, Arial, sans-serif"


class Svg:
    def __init__(self, w, title, desc):
        self.w, self.title, self.desc, self.parts = w, title, desc, []

    def rect(self, x, y, w, h, fill=WHITE, stroke=LINE, rx=8, sw=1.2):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def text(self, x, y, s, size=13, weight=400, fill=INK, anchor="start", italic=False):
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        st = ' font-style="italic"' if italic else ""
        self.parts.append(f'<text xml:space="preserve" x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{st}>{s}</text>')

    def lines(self, x, y, items, size=12, gap=17, fill=INK, weight=400):
        for i, s in enumerate(items):
            self.text(x, y + i * gap, s, size, weight, fill)

    def line(self, x1, y1, x2, y2, stroke="#9ca3af", sw=1.5):
        self.parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"/>')

    def save(self, name, h):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {h}" width="{self.w}" height="{h}" '
               f'font-family="{FONT}" role="img" aria-label="{self.desc}"><title>{self.title}</title>'
               f'<rect width="{self.w}" height="{h}" rx="12" fill="{WHITE}" stroke="{LINE}"/>' + "".join(self.parts) + "</svg>")
        with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(svg)
        print("wrote", name, f"{self.w}x{h}")


# ---------------------------------------------------------------- 1. hierarchy: one class vs base + subclasses
def hierarchy():
    s = Svg(980, "Stage design options", "Option A: one Stage class with keywords. Option B, decided: a base Stage class with Transient and Permanent subclasses.")
    s.text(24, 34, "Decision 1: how should a stage declare what kind of work it does?", 17, 700)
    s.text(24, 56, "Decided: Option B with two kinds. Nothing is implemented. Memory cost is the same: classes exist once per stage, never per item.", 12, 400, MUTED)

    ax, ay, aw, ah = 24, 76, 440, 420
    s.rect(ax, ay, aw, ah, GREY_BG)
    s.text(ax + 16, ay + 28, "Option A: one class, keywords (not chosen)", 15, 700, BLUE)
    s.rect(ax + 16, ay + 46, aw - 32, 140, WHITE, BLUE)
    s.lines(ax + 28, ay + 70, ["Stage(name, fn, workers=3,", "      effect=\"permanent\",   # transient | permanent",
                               "      save=True, retries=0,", "      recover=lambda ctx: ...)"], 12, 18)
    s.text(ax + 16, ay + 214, "The runner decides what each value of effect= means:", 12, 600)
    s.lines(ax + 16, ay + 236, ["if effect == \"permanent\": never retry; markers; ...", "else: retry, redo, drop freely; ...", "(one switch, in the runner)"], 12, 17, MUTED)
    s.text(ax + 16, ay + 302, "Good", 13, 700, GREEN)
    s.lines(ax + 16, ay + 322, ["+ Smallest API: one name to learn", "+ Nothing new to import"], 12, 17)
    s.text(ax + 16, ay + 360, "Costly", 13, 700, RED)
    s.lines(ax + 16, ay + 380, ["- Behaviour lives in one switch; users cannot add a kind"], 12, 17)

    bx, by, bw, bh = 500, 76, 456, 420
    s.rect(bx, by, bw, bh, GREY_BG)
    s.text(bx + 16, by + 28, "Option B: base class + a subclass per kind (chosen)", 15, 700, GREEN)
    s.text(bx + 16, by + 48, "behaviour lives ON the classes (retry, resume, stop, validate)", 12, 400, MUTED)
    s.rect(bx + 118, by + 62, 220, 70, WHITE, "#6b7280")
    s.text(bx + 228, by + 82, "Stage", 14, 700, INK, "middle")
    s.text(bx + 228, by + 100, "undeclared = today's behaviour", 11, 400, MUTED, "middle")
    s.text(bx + 228, by + 117, "no retries or checkpoint allowed", 11, 400, RED, "middle")
    kids = [("Transient", GREEN, GREEN_BG, ["leaves no trace", "retry, redo, drop: yes", "scratch=True: own files", "cleaned up for you"]),
            ("Permanent", RED, RED_BG, ["leaves a lasting change", "never repeated on its own", "markers: .start .done", "idempotent=True or recover="])]
    cx = [bx + 24, bx + 240]
    for (name, col, bg, items), x in zip(kids, cx):
        s.line(bx + 228, by + 132, x + 96, by + 162)
        s.rect(x, by + 162, 192, 124, bg, col)
        s.text(x + 96, by + 183, name, 14, 700, col, "middle")
        s.lines(x + 10, by + 207, items, 11, 18, INK)
    s.text(bx + 16, by + 320, "Good", 13, 700, GREEN)
    s.lines(bx + 16, by + 338, ["+ Each kind owns and tests its own rules", "+ Users can subclass for their own kind", "+ Plain Stage keeps working unchanged"], 12, 16)
    s.text(bx + 250, by + 320, "Costly", 13, 700, RED)
    s.lines(bx + 250, by + 338, ["- Two more public names", "- Kind is fixed by inheritance"], 12, 16)
    s.text(bx + 16, by + 404, "Rule: does it leave a permanent trace? If yes, Permanent.", 11.5, 600, GREEN)
    s.save("stage-hierarchy.svg", 516)


# ---------------------------------------------------------------- 2. effect matrix
def matrix():
    s = Svg(980, "What the library may do, by kind of stage", "A grid of stage kinds against retry, re-run on resume, skip on resume, behaviour at a stop, and what the user must provide.")
    s.text(24, 34, "What the library may do automatically, by kind of stage", 17, 700)
    s.text(24, 56, "Proposed. Green: yes. Amber: only under a condition. Red: never (the user must handle it).", 12, 400, MUTED)
    cols = [("Kind of stage", 210), ("Retry in a run", 130), ("Re-run on resume", 150), ("Skip on resume", 150), ("At a graceful stop", 170), ("You must provide", 114)]
    x0, y0 = 24, 76
    x = x0
    for name, w in cols:
        s.rect(x, y0, w, 34, "#e5e7eb", LINE, 4)
        s.text(x + 8, y0 + 22, name, 12, 700)
        x += w
    G, A, R, N = (GREEN_BG, GREEN), (AMBER_BG, AMBER), (RED_BG, RED), (GREY_BG, MUTED)
    rows = [
        ("Stage (undeclared)", "today's behaviour", [("not allowed", R), ("not allowed", R), ("not allowed", R), ("finish in flight", N), ("declare a kind", N)]),
        ("Transient", "reads, parses, OCR: no trace", [("yes", G), ("yes", G), ("if output saved", A), ("dropped, recomputed", G), ("nothing", N)]),
        ("Transient, scratch=True", "private working files", [("yes, overwrite", G), ("yes", G), ("if output saved", A), ("dropped, recomputed", G), ("nothing (cleanup)", N)]),
        ("Permanent, idempotent=True", "atomic write, upsert", [("yes", G), ("yes", G), ("if .done marker", A), ("finishes the call", A), ("the promise", N)]),
        ("Permanent", "delete, send, bill, append", [("never", R), ("only via recover=", A), ("if .done or recover says so", A), ("finishes the call", A), ("recover=", N)]),
    ]
    y = y0 + 40
    for kind, sub, cells in rows:
        s.rect(x0, y, 210, 50, WHITE, LINE, 4)
        s.text(x0 + 8, y + 21, kind, 12.5, 700)
        s.text(x0 + 8, y + 38, sub, 11, 400, MUTED)
        x = x0 + 210
        for (label, (bg, fg)), (name, w) in zip(cells, cols[1:]):
            s.rect(x, y, w, 50, bg, LINE, 4)
            s.text(x + w / 2, y + 30, label, 11.5, 600, fg, "middle")
            x += w
        y += 56
    s.text(24, y + 14, "Retry is a separate opt-in (retries=N), off by default. Skipping a stage needs its output saved, or recover= must supply a value.", 12, 400, MUTED)
    s.text(24, y + 34, "A Permanent stage must be first or follow a saved stage (its input must still exist on a restart), unless recover= says otherwise.", 12, 400, MUTED)
    s.save("effect-matrix.svg", y + 52)


# ---------------------------------------------------------------- 3. stop modes
def stop_modes():
    s = Svg(980, "What happens to in-flight items at a graceful stop", "Five example items at the moment a stop is requested, and what each stop mode does with them.")
    s.text(24, 34, "Decision 3: what happens to items already in the pipeline when a stop is requested", 17, 700)
    s.text(24, 56, "Example pipeline: read (Transient, not saved) -> upload (Permanent, not saved) -> save (Transient, saved). A stop arrives at this moment:", 12, 400, MUTED)
    cols = [("Item, at the moment of the stop", 260), ("No drain  (drain=False)", 220), ("Automatic  (drain=None)", 240), ("Drain everything  (drain=True)", 224)]
    x0, y0 = 24, 76
    x = x0
    for name, w in cols:
        s.rect(x, y0, w, 34, "#e5e7eb", LINE, 4)
        s.text(x + 8, y0 + 22, name, 12, 700)
        x += w
    G, A, R, N = (GREEN_BG, GREEN), (AMBER_BG, AMBER), (RED_BG, RED), (GREY_BG, MUTED)
    D = ("dropped, redone later", R)
    rows = [
        ("1  waiting for read", "nothing started, no effect yet", [D, D, ("runs to the end", G)]),
        ("2  read is running", "no effect yet", [("call ends, then dropped", R), ("call ends, then dropped", R), ("runs to the end", G)]),
        ("3  waiting for upload", "read done, value only in memory", [D, D, ("uploads, then saves", G)]),
        ("4  upload is running", "the effect is happening", [("call ends, marker written, dropped", A), ("call ends, goes on to save", G), ("call ends, goes on to save", G)]),
        ("5  waiting for save", "upload done, result only in memory", [("dropped; recover= decides on restart", A), ("goes on to save", G), ("goes on to save", G)]),
    ]
    y = y0 + 40
    widths = [c[1] for c in cols]
    for item, sub, cells in rows:
        s.rect(x0, y, widths[0], 56, WHITE, LINE, 4)
        s.text(x0 + 8, y + 23, item, 12.5, 700)
        s.text(x0 + 8, y + 41, sub, 11, 400, MUTED)
        x = x0 + widths[0]
        for (label, (bg, fg)), w in zip(cells, widths[1:]):
            s.rect(x, y, w, 56, bg, LINE, 4)
            words, line1, line2 = label.split(), "", ""
            for wd in words:
                if len(line1) + len(wd) < 27 and not line2:
                    line1 += (" " if line1 else "") + wd
                else:
                    line2 += (" " if line2 else "") + wd
            if line2:
                s.text(x + w / 2, y + 24, line1, 11.5, 600, fg, "middle")
                s.text(x + w / 2, y + 41, line2, 11.5, 600, fg, "middle")
            else:
                s.text(x + w / 2, y + 33, line1, 11.5, 600, fg, "middle")
            x += w
        y += 62
    s.text(24, y + 14, "Key: green = keeps going.  Red = dropped (not an error: nothing is written, the item is redone on the next run).  Amber = depends on a marker or recover=.", 12, 600, INK)
    y += 22
    s.text(24, y + 14, "Automatic rule: if an effect already happened and its result exists only in memory, the item keeps going until its data is saved.", 12, 600, MUTED)
    s.text(24, y + 34, "Otherwise it is dropped at the next stage boundary. A call that is already running always finishes. Dropped items write nothing.", 12, 400, MUTED)
    s.text(24, y + 54, "Drain everything also starts the upload for items 1 to 3: that is what you ask for with drain=True. Automatic never starts a new effect.", 12, 400, MUTED)
    s.save("stop-modes.svg", y + 74)


# ---------------------------------------------------------------- 4. priority lanes
LEVELS = [
    ("URGENT", "goes first, may preempt", RED_BG, RED),
    ("HIGH", "share 6", AMBER_BG, AMBER),
    ("NORMAL", "share 3 (default)", BLUE_BG, BLUE),
    ("LOW", "share 1", GREEN_BG, GREEN),
]


def priority_lanes():
    s = Svg(980, "Priority lanes", "An item gets a priority level when it is admitted and keeps it through every stage queue. Each queue serves urgent first, then the other levels by their share, and an item that has been overtaken too often moves to the front of its level.")
    s.text(24, 34, "Proposal: priority levels, shares and a limit on losing your place (issue #19)", 17, 700)
    s.text(24, 56, "Nothing here is built. Memory stays bounded by the window: a queue holds only items already admitted, and a level is one small integer per item.", 12, 400, MUTED)
    cols = [("Admission", "pulls the next input", 24), ("Stage 1 queue", "e.g. download", 350), ("Stage 2 queue", "e.g. ocr", 676)]
    chips = {
        0: [["u1"], ["u1"], []],
        1: [["h1", "h2"], ["h2", "h3"], ["h1"]],
        2: [["n1", "n2", "n3"], ["n1", "n2"], ["n3"]],
        3: [["l1", "l2"], ["l1"], ["l2"]],
    }
    top, lane_h, col_w = 80, 70, 280
    for ci, (title, sub, x) in enumerate(cols):
        s.text(x, top + 8, title, 14, 700)
        s.text(x + 120, top + 8, sub, 11, 400, MUTED)
        for li, (name, note, bg, fg) in enumerate(LEVELS):
            y = top + 22 + li * lane_h
            s.rect(x, y, col_w, lane_h - 8, bg, fg, 6, 1.2)
            s.text(x + 10, y + 20, name, 12, 700, fg)
            s.text(x + 10, y + 36, note, 10.5, 400, MUTED)
            cx = x + 130
            for c in chips[li][ci]:
                s.rect(cx, y + 12, 34, 28, WHITE, fg, 4, 1.2)
                s.text(cx + 17, y + 31, c, 12, 600, fg, "middle")
                cx += 40
        if ci < 2:
            ay = top + 22 + 2 * lane_h
            s.line(x + col_w + 4, ay, x + col_w + 40, ay, INK, 2)
            s.text(x + col_w + 22, ay - 6, "no barrier", 10, 400, MUTED, "middle")
    y = top + 22 + 4 * lane_h + 10
    s.rect(24, y, 932, 150, GREY_BG, LINE, 8)
    s.text(40, y + 26, "How a stage picks its next item", 14, 700)
    s.lines(40, y + 50, [
        "1.  URGENT first, always (and it may preempt: see the second diagram).",
        "2.  Then the levels take turns by share: out of every 10 picks HIGH gets 6, NORMAL 3, LOW 1, so LOW is never starved.",
        "3.  Inside a level, first in first out. Each time an item is overtaken it counts a loss. After max_bypass losses it moves to the front of its level.",
        "4.  The level is set once, at admission, and the item carries it through every stage. A stage starts an item the moment the previous stage hands it over.",
        "5.  ordered=True keeps input order inside one level. Results of different levels may overtake each other.",
    ], 12, 18)
    s.save("priority-lanes.svg", y + 170)


def wrap(text, n):
    out, ln = [], ""
    for wd in text.split():
        if len(ln) + len(wd) > n:
            out.append(ln)
            ln = wd
        else:
            ln = (ln + " " + wd).strip()
    out.append(ln)
    return out


# ---------------------------------------------------------------- 5. what URGENT can do to a running call
def priority_urgent():
    s = Svg(980, "Urgent items and running calls", "An urgent item never waits behind lower levels in a queue. A running call is only preempted if its stage is Transient and the stage allows it; Permanent and undeclared stages are never interrupted.")
    s.text(24, 34, "Proposal: what an URGENT item can do to work that is already running", 17, 700)
    s.text(24, 56, "A Python thread cannot be killed. 'Preempt' means: abandon the call, discard its result, put the item back. Nothing is built.", 12, 400, MUTED)
    cols = [("Stage kind of the running call", 250), ("What URGENT does", 330), ("Why / cost", 352)]
    x0, y0 = 24, 78
    x = x0
    for label, w in cols:
        s.rect(x, y0, w, 30, INK, INK, 4)
        s.text(x + 10, y0 + 20, label, 12, 700, WHITE)
        x += w
    rows = [
        ("Transient", "leaves no trace", ("Preempts it: the call is abandoned, the item goes back to its lane and is redone", GREEN_BG, GREEN),
         "Safe to repeat. Costs a leaked thread until it returns (same machinery as the deadline work, #17). Counts one loss for the item."),
        ("Permanent", "a lasting change", ("Never preempted. URGENT takes the next free worker", RED_BG, RED),
         "Interrupting could leave a half-done effect. The wait is at most one call."),
        ("Plain Stage", "undeclared", ("Never preempted, treated like Permanent", AMBER_BG, AMBER),
         "We do not know that it is safe to repeat, so we assume it is not."),
        ("Not started yet", "waiting in a queue", ("Goes to the front of the queue, ahead of every other level", GREEN_BG, GREEN),
         "Costs nothing. Items it overtakes each count one loss."),
    ]
    y = y0 + 38
    for kind, sub, (a, abg, afg), b in rows:
        s.rect(x0, y, 250, 62, WHITE, LINE, 4)
        s.text(x0 + 10, y + 26, kind, 13, 700)
        s.text(x0 + 10, y + 44, sub, 11, 400, MUTED)
        s.rect(x0 + 250, y, 330, 62, abg, LINE, 4)
        s.lines(x0 + 260, y + 22, wrap(a, 44), 11.5, 15, afg, 600)
        s.rect(x0 + 580, y, 352, 62, WHITE, LINE, 4)
        s.lines(x0 + 590, y + 20, wrap(b, 50), 11, 14, MUTED)
        y += 68
    s.rect(24, y + 6, 932, 92, GREY_BG, LINE, 8)
    s.lines(40, y + 30, [
        "No reserved slots and the window does not grow. A full window gives URGENT the place of the lowest-level item that has not started a call.",
        "No endless losing: an item that has lost its place max_bypass times can no longer be overtaken or preempted. It runs next.",
        "Checkpoints still hold: a preempted item writes nothing and is redone, a saved result is never discarded.",
    ], 12, 20)
    s.save("priority-urgent.svg", y + 116)


hierarchy()
matrix()
stop_modes()
priority_lanes()
priority_urgent()
