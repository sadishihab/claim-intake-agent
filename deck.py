"""Build deck.pdf, the seven-slide submission deck.

Not part of the app. Needs reportlab, which is deliberately not in
requirements.txt:

    python3 -m venv /tmp/deckenv && /tmp/deckenv/bin/pip install reportlab
    /tmp/deckenv/bin/python deck.py

Every value, transcript and log line here is copied from SUBMISSION.md,
README.md and the call logs. Nothing on a slide claims more than those do.
"""

from reportlab.lib.colors import HexColor, white
from reportlab.pdfgen import canvas

W, H = 960, 540                      # 16:9
M = 56                               # side margin

INK, DIM, LINE, PALE = (HexColor(c) for c in ("#111111", "#555555", "#c8c8c8", "#f6f6f6"))
OK_FG, OK_BG, OK_ED = (HexColor(c) for c in ("#0b5c2a", "#e3f3e8", "#1a7f3d"))
UN_FG, UN_BG, UN_ED = (HexColor(c) for c in ("#7a4600", "#fbeed6", "#a86a00"))
RJ_FG, RJ_BG, RJ_ED = (HexColor(c) for c in ("#971b13", "#fbe3e1", "#c0271c"))

BOLD, REG, MONO, MONOB = "Helvetica-Bold", "Helvetica", "Courier", "Courier-Bold"

overflows = []


def t(c, x, y, s, font=REG, size=17, color=INK, limit=None):
    """Draw one line. Records anything wider than the space it was given."""
    if limit and c.stringWidth(s, font, size) > limit:
        overflows.append((round(c.stringWidth(s, font, size)), limit, s[:60]))
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawString(x, y, s)


def right(c, x, y, s, font=REG, size=17, color=INK):
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawRightString(x, y, s)


def label(c, x, y, s, color=DIM, size=11):
    """Small all-caps section marker, letterspaced by hand."""
    c.setFont(BOLD, size)
    c.setFillColor(color)
    c.drawString(x, y, " ".join(s.upper()))


def box(c, x, y, w, h, fill=None, edge=INK, width=2, r=4):
    c.setLineWidth(width)
    c.setStrokeColor(edge)
    if fill is not None:
        c.setFillColor(fill)
    c.roundRect(x, y, w, h, r, stroke=1, fill=fill is not None)


def arrow(c, x, y0, y1, color=INK):
    """Short vertical arrow, pointing down."""
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(2)
    c.line(x, y0, x, y1 + 7)
    c.setLineWidth(1)
    p = c.beginPath()
    p.moveTo(x - 5, y1 + 8)
    p.lineTo(x + 5, y1 + 8)
    p.lineTo(x, y1)
    p.close()
    c.drawPath(p, stroke=0, fill=1)


def arrow_right(c, x0, x1, y, color=INK):
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(2)
    c.line(x0, y, x1 - 7, y)
    c.setLineWidth(1)
    p = c.beginPath()
    p.moveTo(x1 - 8, y - 5)
    p.lineTo(x1 - 8, y + 5)
    p.lineTo(x1, y)
    p.close()
    c.drawPath(p, stroke=0, fill=1)


def chrome(c, n, title):
    """Standard slide furniture. Returns the y of the first content line."""
    t(c, M, H - 92, title, BOLD, 34, INK, limit=W - 2 * M)
    c.setStrokeColor(LINE)
    c.setLineWidth(2)
    c.line(M, H - 116, W - M, H - 116)
    right(c, W - M, 30, str(n), REG, 11, DIM)
    t(c, M, 30, "The claim intake agent that refuses to guess", REG, 11, DIM)
    return H - 158


def page(c):
    c.setFillColor(white)
    c.rect(0, 0, W, H, stroke=0, fill=1)


# --- 1. title ------------------------------------------------------------

def slide_title(c):
    page(c)
    t(c, M, 352, "The claim intake agent", BOLD, 52)
    t(c, M, 292, "that refuses to guess", BOLD, 52)
    c.setStrokeColor(INK)
    c.setLineWidth(3)
    c.line(M, 260, M + 190, 260)
    t(c, M, 216, "Voice claim intake on the AssemblyAI Voice Agent API", REG, 21, INK)
    t(c, M, 184, "It cannot write a value into the record unless a server-side",
      REG, 18, DIM)
    t(c, M, 160, "validator approves it.", REG, 18, DIM)
    t(c, M, 78, "github.com/sadishihab/claim-intake-agent", MONO, 13, DIM)
    t(c, M, 58, "web-production-74b77.up.railway.app", MONO, 13, DIM)


# --- 2. the problem ------------------------------------------------------

def slide_problem(c):
    page(c)
    y = chrome(c, 2, "The problem")

    label(c, M, y, "one policy number, read aloud")
    box(c, M, y - 168, 392, 152, PALE, LINE, 1)
    for i, line in enumerate(["3841188", "M-A-C-K-K-K-D-4-1-1-3-8", "D411",
                              "KD41118", "KD41282"]):
        t(c, M + 18, y - 44 - i * 26, line, MONO, 16, INK, limit=356)
    t(c, M, y - 192, "All rejected. None is a policy on file.", REG, 15, DIM)

    x2 = M + 448
    label(c, x2, y, "the harder case")
    box(c, x2, y - 100, 400, 84, PALE, LINE, 1)
    t(c, x2 + 18, y - 44, "BX7-4402", MONOB, 17)
    t(c, x2 + 140, y - 44, "Marcus Halloway", REG, 17)
    t(c, x2 + 18, y - 76, "BX7-4420", MONOB, 17)
    t(c, x2 + 140, y - 76, "Priya Raghunathan", REG, 17)
    t(c, x2, y - 128, "One transposition apart. Two different people.", REG, 16, DIM)

    box(c, x2, y - 232, 400, 76, RJ_BG, RJ_ED, 2)
    t(c, x2 + 18, y - 186, "A wrong value that looks right", BOLD, 18, RJ_FG)
    t(c, x2 + 18, y - 212, "gets filed and never surfaces.", BOLD, 18, RJ_FG)


# --- 3. the architecture -------------------------------------------------

def slide_architecture(c):
    page(c)
    chrome(c, 3, "The architecture")

    x, w, h = 90, 470, 46
    steps = [
        (356, "Caller speaks", None),
        (288, "AssemblyAI Voice Agent API", "WebSocket, PCM16 mono 24 kHz"),
        (220, "agent calls record_field(field, value)", "the only way to write anything"),
        (152, "validators.py", "returns status, reason, readback"),
    ]
    for i, (y, head, sub) in enumerate(steps):
        fill = PALE if i != 3 else OK_BG
        edge = INK if i != 3 else OK_ED
        box(c, x, y, w, h, fill, edge, 2)
        if sub:
            t(c, x + 18, y + 26, head, BOLD, 16, INK, limit=w - 36)
            t(c, x + 18, y + 10, sub, REG, 12, DIM, limit=w - 36)
        else:
            t(c, x + 18, y + 17, head, BOLD, 16, INK, limit=w - 36)
        if i:
            arrow(c, x + w / 2, steps[i - 1][0], y + h)

    box(c, 600, 152, 304, 46, PALE, LINE, 2)
    t(c, 618, 178, "calls/<session_id>.jsonl", MONOB, 13, INK, limit=268)
    t(c, 618, 162, "every attempt, as it happens", REG, 12, DIM)
    arrow_right(c, x + w, 600, 175)

    t(c, M, 112, "The model proposes a value. It never writes one.", BOLD, 19)
    t(c, M, 84, "The verdict carries the exact words to speak, and the prompt "
                "requires them verbatim.", REG, 15, DIM, limit=W - 2 * M)
    t(c, M, 62, "Audio is proxied through the server, so the API key never reaches "
                "the browser.", REG, 15, DIM, limit=W - 2 * M)


# --- 4. the three verdicts -----------------------------------------------

def slide_verdicts(c):
    page(c)
    y = chrome(c, 4, "The three verdicts")

    rows = [
        ("accepted", "checked and unambiguous",
         "records it, says nothing, moves on", OK_FG, OK_BG, OK_ED),
        ("unconfirmed", "probably right, cannot be sure",
         "reads the value back and waits", UN_FG, UN_BG, UN_ED),
        ("rejected", "cannot be right",
         "explains the problem and asks again", RJ_FG, RJ_BG, RJ_ED),
    ]
    for i, (name, means, does, fg, bg, ed) in enumerate(rows):
        top = y - 8 - i * 80
        box(c, M, top - 58, W - 2 * M, 58, bg, ed, 2)
        t(c, M + 20, top - 38, name, MONOB, 19, fg, limit=180)
        t(c, M + 220, top - 38, means, REG, 17, INK, limit=290)
        t(c, M + 528, top - 38, does, REG, 17, DIM, limit=300)

    t(c, M, 124, "The middle one is where the design went.", BOLD, 19, INK, limit=460)
    t(c, M, 100, "A system with only accept and reject guesses on every near miss.",
      REG, 15, DIM, limit=460)

    t(c, M + 500, 124, "BX7-4402", MONOB, 15)
    t(c, M + 610, 124, '"four four zero two"', MONO, 15, DIM)
    t(c, M + 500, 100, "BX7-4420", MONOB, 15)
    t(c, M + 610, 100, '"four four two zero"', MONO, 15, DIM)
    t(c, M + 500, 74, "Two real policies. The readback separates them.", REG, 12, DIM)


# --- 5. what we measured -------------------------------------------------

def slide_measured(c):
    page(c)
    y = chrome(c, 5, "What we measured")
    t(c, M, y, "Three null results on recogniser tuning.", REG, 20, DIM)

    rows = [
        ("Baseline, 12 clips", "12 / 12", "Test at its ceiling. Cannot discriminate."),
        ("Noise at 15, 10 and 5 dB SNR", "no change", "Still transcribed perfectly."),
        ("transcription_prompt, max_accuracy", "no effect", "Not carried."),
    ]
    for i, (lever, result, note) in enumerate(rows):
        top = y - 40 - i * 68
        if i < len(rows) - 1:                       # no rule under the last row
            c.setStrokeColor(LINE)
            c.setLineWidth(1)
            c.line(M, top - 52, W - M, top - 52)
        t(c, M, top - 32, lever, MONOB, 16, INK, limit=330)
        t(c, M + 348, top - 32, result, BOLD, 20, DIM, limit=140)
        t(c, M + 508, top - 32, note, REG, 16, INK, limit=320)

    t(c, M, 132, "The clips were text-to-speech, which cannot produce human "
                 "disfluency.", REG, 14, DIM, limit=W - 2 * M)
    t(c, M, 110, "The harness was a scratch script and is not in the repository.",
      REG, 14, DIM, limit=W - 2 * M)

    box(c, M, 44, W - 2 * M, 50, RJ_BG, RJ_ED, 2)
    t(c, M + 20, 62, "One lever did move something. It made the system worse.",
      BOLD, 19, RJ_FG, limit=W - 2 * M - 40)


# --- 6. the keyterms finding ---------------------------------------------

def slide_keyterms(c):
    page(c)
    y = chrome(c, 6, "The keyterms finding")
    t(c, M, y, "input.keyterms biased transcription toward the policy numbers "
               "in policies.json.", REG, 18, DIM, limit=W - 2 * M)

    box(c, M, y - 152, W - 2 * M, 130, PALE, LINE, 1)
    lines = [
        ("partial", "Yes, my policy number is C411.", DIM, ""),
        ("partial", "Yes, my policy number is KD4-1188.", INK, "<- revised onto a keyterm"),
        ("FINAL", "Yes, my policy number is KD4-1188.", INK, ""),
        ("recorded", "KD4-1188   accepted   exact match on file", RJ_FG, ""),
    ]
    for i, (kind, text_, color, tail) in enumerate(lines):
        yy = y - 48 - i * 28
        t(c, M + 18, yy, kind, MONOB, 14, DIM)
        t(c, M + 110, yy, text_, MONO, 15, color, limit=440)
        if tail:
            t(c, M + 566, yy, tail, MONOB, 13, RJ_FG, limit=270)

    box(c, M, y - 268, W - 2 * M, 96, white, INK, 3)
    t(c, M + 24, y - 202, "Never bias the recogniser toward the answer key", BOLD, 21)
    t(c, M + 24, y - 232, "when the recogniser's output is the evidence being "
                          "validated.", BOLD, 21, INK, limit=W - 2 * M - 48)

    t(c, M, 96, "Every downstream guard still passed. Exact match was only evidence "
                "while the", REG, 15, DIM, limit=W - 2 * M)
    t(c, M, 76, "recogniser knew nothing about the policy list.", REG, 15, DIM)
    t(c, M, 54, "The audio is not kept. That the number read was wrong is my "
                "recollection, not something the log proves.", REG, 11, DIM,
      limit=W - 2 * M)


# --- 7. the evidence trail -----------------------------------------------

def slide_evidence(c):
    page(c)
    y = chrome(c, 7, "The evidence trail")
    t(c, M, y, "Every attempt is appended to calls/<session_id>.jsonl as it "
               "happens.", REG, 18, DIM, limit=W - 2 * M)

    box(c, M, y - 122, W - 2 * M, 100, UN_BG, UN_ED, 2)
    t(c, M + 20, y - 48, "25.0s", MONOB, 15, UN_FG)
    t(c, M + 100, y - 48, "policy_number   KD41188", MONO, 15, INK)
    t(c, M + 400, y - 48, "UNCONFIRMED", MONOB, 15, UN_FG)
    t(c, M + 100, y - 74, 'heard      "My policy number is KD41188."', MONO, 14, DIM,
      limit=700)
    t(c, M + 100, y - 100, 'readback   "That\'s Kilo Delta four, one one eight '
                           'eight, correct?"', MONO, 14, DIM, limit=700)

    box(c, M, y - 216, W - 2 * M, 74, OK_BG, OK_ED, 2)
    t(c, M + 20, y - 168, "41.5s", MONOB, 15, OK_FG)
    t(c, M + 100, y - 168, "policy_number   KD41188", MONO, 15, INK)
    t(c, M + 400, y - 168, "ACCEPTED", MONOB, 15, OK_FG)
    t(c, M + 560, y - 168, "-> KD4-1188", MONO, 15, OK_FG)
    t(c, M + 100, y - 194, 'heard      "Yes, correct."', MONO, 14, DIM)

    t(c, M, 128, "A policy number is never accepted on a match alone.", BOLD, 19)
    t(c, M, 104, "The caller has to hear it read back and agree. ClaimRecord "
                 "enforces that, not the prompt.", REG, 15, DIM, limit=W - 2 * M)

    for i, fact in enumerate(["/compare renders six moments side by side.",
                              "The panel at / tails the same log.",
                              "196 tests."]):
        t(c, M + i * 300, 58, fact, REG, 14, DIM, limit=290)


def main():
    c = canvas.Canvas("deck.pdf", pagesize=(W, H))
    c.setTitle("The claim intake agent that refuses to guess")
    for fn in (slide_title, slide_problem, slide_architecture, slide_verdicts,
               slide_measured, slide_keyterms, slide_evidence):
        fn(c)
        c.showPage()
    c.save()
    for got, allowed, s in overflows:
        print(f"[overflow] {got}pt > {allowed}pt: {s}")
    print(f"deck.pdf written, 7 slides, {len(overflows)} overflow(s)")


if __name__ == "__main__":
    main()
