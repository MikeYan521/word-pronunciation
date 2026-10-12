"""Generate a small sample textbook PDF so the app can be tried immediately.

    python tools/make_sample_pdf.py [output.pdf]

The generator is also used by the test-suite, which is why it lives in a module
instead of being inlined into a test.
"""

from __future__ import annotations

import sys
from pathlib import Path

CHAPTERS = [
    (
        "Chapter 1  Welcome Back to School",
        [
            "A new semester begins and every student is excited about the coming year. "
            "The teacher introduces the syllabus and explains the assessment system. "
            "Students discuss their holiday experiences in small groups.",
            "Vocabulary in context: environment, government, opportunity, knowledge, "
            "necessary, schedule, technology, celebrate, describe, ancient.",
        ],
    ),
    (
        "Chapter 2  Amazing Animals",
        [
            "Many animals have remarkable abilities that help them survive in the wild. "
            "The research team observes the behaviour of elephants and dolphins. "
            "Their communication system is surprisingly complex.",
            "Vocabulary in context: environment, behaviour, remarkable, survive, "
            "communication, complex, observe, ability, wild, research.",
        ],
    ),
    (
        "Chapter 3  Technology and Life",
        [
            "Technology changes the way we work, learn and communicate. "
            "Engineers develop new devices every year. "
            "However, we must consider the influence of technology on our health.",
            "Vocabulary in context: technology, influence, develop, device, engineer, "
            "health, communicate, improve, decision, culture.",
        ],
    ),
]


def build_pdf(path: str | Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4

    def page_number(number: int) -> None:
        pdf.setFont("Helvetica", 9)
        pdf.drawString(width / 2, 30, str(number))

    # cover
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(72, height - 200, "English Textbook (Sample)")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, height - 230, "Generated for local testing only.")
    page_number(1)
    pdf.showPage()

    # table of contents
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(72, height - 100, "Contents")
    pdf.setFont("Helvetica", 11)
    for index, (title, _) in enumerate(CHAPTERS):
        pdf.drawString(90, height - 140 - index * 20, f"{title} .......... {3 + index * 2}")
    page_number(2)
    pdf.showPage()

    page = 3
    for title, paragraphs in CHAPTERS:
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(72, height - 90, title)
        pdf.setFont("Helvetica", 11)
        y = height - 140
        for paragraph in paragraphs:
            for line in _wrap(paragraph, 92):
                pdf.drawString(72, y, line)
                y -= 18
            y -= 10
        page_number(page)
        pdf.showPage()
        page += 1

        # a second page per chapter so the page range spans more than one page
        pdf.setFont("Helvetica", 11)
        body = (
            "Exercise: read the passage again and underline the words you do not know. "
            "Then listen to the recording and repeat each sentence aloud. "
            "The teacher will explain the grammar points in the next lesson."
        )
        y = height - 100
        for line in _wrap(body, 92):
            pdf.drawString(72, y, line)
            y -= 18
        page_number(page)
        pdf.showPage()
        page += 1

    # back matter that must be excluded from vocabulary
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(72, height - 100, "References")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, height - 130, "Doe, J. (2020). A study of vocabulary acquisition. Oxford.")
    page_number(page)
    pdf.showPage()

    pdf.save()
    return path


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "work/sample_textbook.pdf"
    out = build_pdf(target)
    print(f"created {out}")
