"""Text of a law from the Chamber's PDF ("Forma pentru promulgare").

The PDFs are made with Word, so they contain real text (no OCR needed). One
trap: superscript article numbers. "art. 27¹" (an article inserted after 27)
is extracted as "art. 271" by plain tools: a WRONG number that a summary would
repeat. Superscripts are recognised by font size and written as "27^1".

Second trap: some laws (e.g. the Codul penal) put each article's title in a
left margin column; read line by line, it lands in the middle of sentences
("de ce conțin abuzuri sexuale operațiuni de procurare"). The body column is
extracted on its own; margin titles become a separate labelled line.
"""

from __future__ import annotations

import io
import re
import statistics

import pdfplumber
from pdfplumber.utils import extract_text

SUPERSCRIPT_RATIO = 0.8  # 10.6 pt superscripts on 16 pt body text in real PDFs


def _body_left_edge(page) -> float | None:
    """x where the body column starts, if the page has a separate margin column.

    Lines of body text start at (almost) the same x; margin words end well
    before it. None = single column."""
    words = page.extract_words(x_tolerance=1.5, y_tolerance=3)
    if len(words) < 20:
        return None
    starts = sorted(min(ws, key=lambda w: w["x0"])["x0"] for ws in _lines(words) if len(ws) >= 4)
    if not starts:
        return None
    left = statistics.median(starts)
    margin = [w for w in words if w["x1"] < left - 4]
    return left if len(margin) >= 3 and left > page.width * 0.25 else None


def _lines(words: list[dict]) -> list[list[dict]]:
    by_top: dict[int, list[dict]] = {}
    for w in words:
        by_top.setdefault(round(w["top"] / 3), []).append(w)
    return list(by_top.values())


def _margin_char_ids(page, left: float) -> set[int]:
    """Chars that belong to the margin column: on lines that are really split,
    i.e. with a visible gap between the margin part and the body part. A line
    that runs on from the margin into the body (e.g. "2. Articolul 374 va avea
    următorul cuprins:") is one full-width line and stays in the body."""
    words = page.extract_words(x_tolerance=1.5, y_tolerance=3, return_chars=True)
    ids: set[int] = set()
    for line in _lines(words):
        lhs = [w for w in line if w["x1"] < left - 4]
        rhs = [w for w in line if w["x1"] >= left - 4]
        split = (
            not rhs or min(w["x0"] for w in rhs) - max(w["x1"] for w in lhs) > 8 if lhs else False
        )
        if split:
            for w in lhs:
                ids.update(id(c) for c in w["chars"])
    return ids


def _page_text(page) -> str:
    left = _body_left_edge(page)
    if left is None:
        return _chars_text(list(page.chars))
    margin_ids = _margin_char_ids(page, left)
    # pdfplumber returns the same char dicts in page.chars and in words' chars
    body = _chars_text([c for c in page.chars if id(c) not in margin_ids])
    margin = _chars_text([c for c in page.chars if id(c) in margin_ids])
    margin = " ".join(ln.strip() for ln in margin.splitlines() if ln.strip())
    return body + (f"\n[Titlu marginal: {margin}]" if margin else "")


def _chars_text(page_chars: list[dict]) -> str:
    chars = [dict(c) for c in page_chars]
    sizes = [c["size"] for c in chars if c["text"].strip()]
    if not sizes:
        return ""
    body = statistics.median(sizes)
    previous_was_sup = False
    for c in chars:
        is_sup = c["text"].isdigit() and c["size"] < SUPERSCRIPT_RATIO * body
        if is_sup and not previous_was_sup:
            c["text"] = "^" + c["text"]  # first digit of a superscript run
        previous_was_sup = is_sup
    # y_tolerance: a raised superscript must stay on its line
    text = extract_text(chars, x_tolerance=1.5, y_tolerance=5)
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and re.fullmatch(r"\s*\d{1,3}\s*", lines[-1]):  # page number
        lines.pop()
    while lines and re.fullmatch(r"\s*\d{1,3}\s*", lines[0]):
        lines.pop(0)
    return "\n".join(lines)


def pdf_to_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [_page_text(p) for p in pdf.pages]
    text = "\n".join(p for p in pages if p)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def page_count(data: bytes) -> int:
    """Fast (no text parsing): decides whether a law is too long to explain."""
    import pypdfium2  # installed with pdfplumber

    doc = pypdfium2.PdfDocument(data)
    try:
        return len(doc)
    finally:
        doc.close()


def is_scanned(data: bytes) -> bool:
    """A scan carries only a (bad) OCR text layer: the producer is a scanner."""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        producer = str((pdf.metadata or {}).get("Producer", ""))
    return bool(re.search(r"(?i)scan", producer))


def page_images(data: bytes, scale: float = 1.6, max_pages: int = 30) -> list[bytes]:
    """JPEG of each page, for scanned PDFs: the model reads what a person sees,
    not the scanner's OCR layer ("dejudeca{ă se va desfăşui.a")."""
    import pypdfium2  # installed with pdfplumber

    out = []
    doc = pypdfium2.PdfDocument(data)
    try:
        for i in range(min(len(doc), max_pages)):
            image = doc[i].render(scale=scale).to_pil().convert("L")  # grey: smaller, same text
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=80)
            out.append(buf.getvalue())
    finally:
        doc.close()
    return out
