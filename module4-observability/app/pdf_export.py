from __future__ import annotations

from typing import Iterable, List

import pandas as pd

from client import now_sgt

PAGE_W = 842
PAGE_H = 595
MARGIN = 36
ROW_H = 16
HEADER_H = 18
FONT_SIZE = 9


def _pdf_escape(text: str) -> str:
    cleaned = (
        str(text or "")
        .encode("latin-1", "replace")
        .decode("latin-1")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
    return cleaned[:80]


def _wrap(text: str, width: int) -> str:
    value = str(text or "")
    return value if len(value) <= width else value[: width - 1] + "…"


def attendance_pdf(frame: pd.DataFrame, title: str = "Attendance report") -> bytes:
    columns = list(frame.columns)
    if not columns:
        columns = ["(empty)"]
        frame = pd.DataFrame(columns=columns)

    col_count = max(1, len(columns))
    usable = PAGE_W - 2 * MARGIN
    col_w = usable / col_count
    rows: List[List[str]] = [[_wrap(col, 22) for col in columns]]
    for _, record in frame.iterrows():
        rows.append([_wrap(record.get(col, ""), 22) for col in columns])

    stamp = now_sgt().strftime("%d %b %Y %H:%M SGT")
    pages = _paginate(rows)
    objects: List[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("latin-1"))

    content_ids = []
    for index, page_rows in enumerate(pages):
        content = _page_stream(title, stamp, page_rows, col_w, index + 1, len(pages))
        content_ids.append(content)

    page_objs = []
    first_content_id = 3 + len(pages)
    for i in range(len(pages)):
        page_objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                f"/Resources << /Font << /F1 {first_content_id + len(pages)} 0 R >> >> "
                f"/Contents {first_content_id + i} 0 R >>"
            ).encode("latin-1")
        )
    font_obj = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    all_objs = objects + page_objs + content_ids + [font_obj]
    return _assemble(all_objs)


def _paginate(rows: List[List[str]]) -> List[List[List[str]]]:
    header = rows[0]
    body = rows[1:]
    top = PAGE_H - MARGIN - 48
    usable = top - MARGIN
    per_page = max(1, int((usable - HEADER_H) // ROW_H))
    pages = []
    if not body:
        pages.append([header])
        return pages
    for start in range(0, len(body), per_page):
        pages.append([header] + body[start : start + per_page])
    return pages


def _page_stream(
    title: str,
    stamp: str,
    rows: List[List[str]],
    col_w: float,
    page: int,
    total: int,
) -> bytes:
    commands = [
        "BT",
        "/F1 14 Tf",
        f"1 0 0 1 {MARGIN} {PAGE_H - 28} Tm",
        f"({_pdf_escape(title)}) Tj",
        "/F1 9 Tf",
        f"1 0 0 1 {MARGIN} {PAGE_H - 44} Tm",
        f"(Official record. Generated { _pdf_escape(stamp) }. Page {page} of {total}.) Tj",
        "ET",
    ]
    y = PAGE_H - MARGIN - 58
    for row_i, row in enumerate(rows):
        if row_i == 0:
            commands.append(f"0.12 0.16 0.20 rg {MARGIN} {y - 4} {PAGE_W - 2 * MARGIN} {HEADER_H} re f")
            commands.append("1 1 1 rg")
        else:
            if row_i % 2 == 0:
                commands.append(f"0.95 0.96 0.97 rg {MARGIN} {y - 4} {PAGE_W - 2 * MARGIN} {ROW_H} re f")
            commands.append("0.10 0.12 0.15 rg")
        commands.append("BT /F1 8 Tf")
        x = MARGIN + 4
        for cell in row:
            commands.append(f"1 0 0 1 {x:.1f} {y:.1f} Tm ({_pdf_escape(cell)}) Tj")
            x += col_w
        commands.append("ET")
        y -= ROW_H if row_i else HEADER_H
    stream = "\n".join(commands).encode("latin-1", "replace")
    return b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"


def _assemble(objects: Iterable[bytes]) -> bytes:
    items = list(objects)
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(items, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("latin-1"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_at = len(out)
    out.extend(f"xref\n0 {len(items) + 1}\n".encode("latin-1"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        (
            f"trailer << /Size {len(items) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n"
        ).encode("latin-1")
    )
    return bytes(out)