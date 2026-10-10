"""Safely remove built-in scanned PDFs from a deployed Maple instance.

The command is intentionally dry-run by default.  Run it on Railway only with
``--apply`` after confirming the printed list.  By default it selects built-in
PDFs with fewer than 1,000 extracted characters, which is a conservative way
to find scanned PDFs.  ``--all-pdf`` is available only when a full reset of
built-in PDFs is intentional.  The command removes matching source PDFs,
stored copies, and related local/cloud indexing records while leaving
Markdown, student uploads, and the rest of the database untouched.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("MPT_DATA_DIR", str(ROOT / "data"))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "platform.db"
DEFAULT_SOURCE = ROOT.parent / "必须要的教材、教师用书和课标"
SOURCE_DIR = Path(os.environ.get("MPT_KNOWLEDGE_SOURCE_DIR", str(DEFAULT_SOURCE))).resolve()


def safe_child(parent: Path, name: str) -> Path | None:
    """Return a direct child path, never a path that escapes its directory."""
    candidate = (parent / name).resolve()
    return candidate if candidate.parent == parent.resolve() else None


def builtin_pdf_rows(connection: sqlite3.Connection, *, all_pdf: bool, min_text_chars: int) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    condition = "" if all_pdf else " AND length(trim(extracted_text)) < ?"
    params = () if all_pdf else (min_text_chars,)
    return list(connection.execute(
        "SELECT id,original_name,stored_name,size,length(trim(extracted_text)) AS text_chars "
        "FROM files WHERE uploaded_by='builtin-knowledge' AND "
        "(lower(mime)='application/pdf' OR lower(original_name) LIKE '%.pdf')" +
        condition + " ORDER BY original_name", params
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description="清理平台知识库中的扫描版 PDF")
    parser.add_argument(
        "--apply", action="store_true",
        help="真正删除；不加此参数只显示将要处理的文件",
    )
    parser.add_argument(
        "--all-pdf", action="store_true",
        help="选择全部内置 PDF；默认只选择提取文字少于1000字的 PDF",
    )
    parser.add_argument(
        "--min-text-chars", type=int, default=1000,
        help="判断扫描版的文字数量上限，默认1000",
    )
    args = parser.parse_args()
    if args.min_text_chars < 0:
        raise SystemExit("--min-text-chars 不能小于0")

    if not DB_PATH.is_file():
        raise SystemExit(f"找不到平台数据库：{DB_PATH}")
    if not SOURCE_DIR.is_dir():
        raise SystemExit(f"找不到知识库目录：{SOURCE_DIR}")

    with sqlite3.connect(DB_PATH) as connection:
        rows = builtin_pdf_rows(
            connection, all_pdf=args.all_pdf, min_text_chars=args.min_text_chars,
        )
        print(f"数据库：{DB_PATH}")
        print(f"知识库目录：{SOURCE_DIR}")
        print(f"待清理扫描版 PDF：{len(rows)} 份")
        for row in rows:
            source = safe_child(SOURCE_DIR, row["original_name"])
            stored = safe_child(UPLOAD_DIR, row["stored_name"])
            source_state = "存在" if source and source.is_file() else "缺失"
            stored_state = "存在" if stored and stored.is_file() else "缺失"
            print(f"- {row['original_name']}（提取文字：{row['text_chars']}字；源文件：{source_state}，平台副本：{stored_state}）")

        if not args.apply:
            print("预览结束：确认列表无误后，再运行同一命令并追加 --apply。")
            return 0
        if not rows:
            print("没有需要清理的扫描版 PDF。")
            return 0

        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            file_id = row["id"]
            stored = safe_child(UPLOAD_DIR, row["stored_name"])
            source = safe_child(SOURCE_DIR, row["original_name"])
            connection.execute("DELETE FROM knowledge_chunks WHERE local_file_id=?", (file_id,))
            connection.execute("DELETE FROM knowledge_provider_files WHERE local_file_id=?", (file_id,))
            connection.execute("DELETE FROM files WHERE id=? AND uploaded_by='builtin-knowledge'", (file_id,))
            if stored and stored.is_file():
                stored.unlink()
            if source and source.is_file() and source.suffix.lower() == ".pdf":
                source.unlink()
        connection.commit()

    print("清理完成：扫描版 PDF、平台副本和对应识别记录已移除；Markdown 与其他数据未改动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
