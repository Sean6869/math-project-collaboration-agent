"""Knowledge readiness must reflect stored evidence, not just cloud upload status."""
import os
import tempfile
import time
import urllib.error
import unittest
from pathlib import Path
from unittest import mock

import platform_server as app


class KnowledgeStatusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in {"DATA_DIR": root, "UPLOAD_DIR": root / "uploads",
                            "DB_PATH": root / "test.db", "SETUP_TOKEN_FILE": root / "token"}.items():
            patch = mock.patch.object(app, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.dict(os.environ, {"ARK_API_KEY": "test-key", "MPT_ENV":"development"})
        patch.start()
        self.addCleanup(patch.stop)
        app.init_database()
        self.addCleanup(app.KNOWLEDGE_SYNC_RUNNING.clear)
        self.addCleanup(app.KNOWLEDGE_PROGRESS.clear)
        self.now = int(time.time())

    def document(self, name="scan", text="", expires=None):
        (app.UPLOAD_DIR / (name + ".pdf")).write_bytes(b"test fixture")
        with app.database() as db:
            db.execute("INSERT INTO files(id,original_name,stored_name,mime,size,extracted_text,uploaded_at,uploaded_by,group_no) VALUES(?,?,?,?,?,?,?,?,NULL)",
                       (name, name + ".pdf", name + ".pdf", "application/pdf", 12,
                        text, app.utc_now(), "builtin-knowledge"))
            db.execute("INSERT INTO knowledge_provider_files VALUES(?,?,?,?,?,?)",
                       (name, "provider-" + name, "active", expires or self.now + 86400, app.utc_now(), ""))

    def chunk(self, status="active", summary="reference concept", expires=None):
        with app.database() as db:
            db.execute("INSERT OR REPLACE INTO knowledge_chunks VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("chunk", "scan", "provider-chunk", 1, 20, status, summary,
                        expires or self.now + 86400, app.utc_now(), "HTTP 429" if status == "failed" else ""))

    @mock.patch.object(app, "pdf_page_count", return_value=100)
    def test_long_active_pdf_is_not_reported_as_ready(self, pages):
        self.document()
        data = app.knowledge_snapshot()
        self.assertFalse(data["files"][0]["searchable"])
        self.assertEqual(data["files"][0]["chunkTotal"], 5)
        self.assertEqual(data["summary"]["indexed"], 0)
        self.assertEqual(app.ark_knowledge_documents({}, "scan"), [])

    @mock.patch.object(app, "pdf_page_count", return_value=100)
    def test_live_transitions_and_partial_failure_preserve_local_retrieval(self, pages):
        self.document()
        app.KNOWLEDGE_SYNC_RUNNING.set()
        self.chunk("processing", "")
        item = app.knowledge_snapshot()["files"][0]
        self.assertEqual(item["taskStatus"], "排队等待分块识别")
        self.assertEqual(item["currentApi"], "")
        self.assertFalse(item["searchable"])
        self.chunk()
        item = app.knowledge_snapshot()["files"][0]
        self.assertEqual(item["statusLabel"], "部分内容可供参考")
        self.assertEqual(item["chunkCount"], 1)
        self.assertIn("reference concept", app.knowledge_context({}, "reference")[0])
        self.assertEqual(app.ark_knowledge_documents({}, "scan")[0]["fileId"], "provider-chunk")
        with app.database() as db:
            db.execute("INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("failed", "scan", "", 21, 40, "failed", "", 0, app.utc_now(), "HTTP 429"))
        item = app.knowledge_snapshot()["files"][0]
        self.assertTrue(item["searchable"])
        self.assertIn("识别服务繁忙或额度不足", item["statusDetail"])
        self.assertEqual(item["taskStatus"], "部分识别失败")

    @mock.patch.object(app, "pdf_page_count", return_value=10)
    def test_expired_whole_document_is_excluded(self, pages):
        self.document(expires=self.now - 10)
        self.assertFalse(app.knowledge_snapshot()["files"][0]["cloudReadable"])
        self.assertEqual(app.ark_knowledge_documents({}, "scan"), [])

    @mock.patch.object(app, "pdf_page_count", return_value=100)
    def test_expired_chunk_keeps_summary_but_cannot_be_sent_to_ark(self, pages):
        self.document()
        self.chunk(expires=self.now - 10)
        item = app.knowledge_snapshot()["files"][0]
        self.assertTrue(item["localSearchable"])
        self.assertFalse(item["cloudReadable"])
        self.assertEqual(app.ark_knowledge_documents({}, "scan"), [])
        self.assertIn("reference concept", app.knowledge_context({}, "reference")[0])

    @mock.patch.object(app, "pdf_page_count", return_value=100)
    def test_completed_chunk_summary_overrides_sparse_pdf_text_label(self, pages):
        self.document(text="少量文字")
        with app.database() as db:
            for index in range(5):
                db.execute(
                    "INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (f"chunk-{index}", "scan", f"provider-{index}", index * 20 + 1,
                     min(100, index * 20 + 20), "active", f"summary {index}",
                     self.now + 86400, app.utc_now(), ""),
                )
        item = app.knowledge_snapshot()["files"][0]
        self.assertEqual(item["chunkCount"], 5)
        self.assertEqual(item["statusLabel"], "内容摘要可供参考")

    def test_somark_markdown_images_are_selected_only_for_visual_questions(self):
        image = "https://example.com/math-figure.jpg"
        markdown = f"# 例题\n![]({image})\n这是图形说明。"
        with app.database() as db:
            db.execute(
                "INSERT INTO files(id,original_name,stored_name,mime,size,extracted_text,uploaded_at,uploaded_by,group_no) VALUES(?,?,?,?,?,?,?,?,NULL)",
                ("somark", "数学笔记.md", "somark.md", "text/markdown", len(markdown),
                 markdown, app.utc_now(), "teacher"),
            )
        self.assertEqual(app.knowledge_image_urls({}, "请看图分析这个几何图形"), [image])
        self.assertEqual(app.knowledge_image_urls({}, "请解释什么是同类项"), [])

    def test_builtin_markdown_is_ready_without_cloud_recognition(self):
        markdown = "# 七年级数学\n这是已经由 SoMark 提取的教材文字。"
        (app.UPLOAD_DIR / "somark-ready.md").write_text(markdown, encoding="utf-8")
        with app.database() as db:
            db.execute(
                "INSERT INTO files(id,original_name,stored_name,mime,size,extracted_text,uploaded_at,uploaded_by,group_no) VALUES(?,?,?,?,?,?,?,?,NULL)",
                ("somark-ready", "数学教材.md", "somark-ready.md", "text/markdown", len(markdown),
                 markdown, app.utc_now(), "builtin-knowledge"),
            )
        item = app.knowledge_snapshot()["files"][0]
        self.assertEqual(item["statusLabel"], "已提取文字可供参考")
        self.assertEqual(item["taskStatus"], "文字内容已就绪")
        self.assertIn("已提取文字，可供AI参考", item["statusDetail"])
        self.assertTrue(item["searchable"])

    @mock.patch.object(app, "pdf_page_count", return_value=10)
    def test_ready_short_document_and_missing_key(self, pages):
        self.document()
        self.assertTrue(app.knowledge_snapshot()["files"][0]["cloudReadable"])
        self.assertEqual(len(app.ark_knowledge_documents({}, "scan")), 1)
        with mock.patch.dict(os.environ, {"ARK_API_KEY": ""}):
            item = app.knowledge_snapshot()["files"][0]
        self.assertFalse(item["searchable"])
        self.assertEqual(item["taskStatus"], "未配置云端识别")

    @mock.patch.object(app, "pdf_page_count", return_value=100)
    def test_processing_record_after_worker_exit_is_not_live_progress(self, pages):
        self.document()
        self.chunk("processing", "")
        app.KNOWLEDGE_SYNC_RUNNING.clear()
        self.assertIn("后台任务已停止", app.knowledge_snapshot()["files"][0]["taskStatus"])

    @mock.patch.object(app, "pdf_page_count", return_value=100)
    def test_large_textbooks_are_not_uploaded_whole_before_chunking(self, pages):
        self.document()
        with mock.patch.object(app, "ark_upload_document") as upload, mock.patch.object(app, "ark_file_status") as status:
            app.sync_ark_knowledge_files()
        upload.assert_not_called()
        status.assert_not_called()

    @mock.patch.object(app, "pdf_page_count", return_value=100)
    def test_only_executing_file_reports_api_operation(self, pages):
        self.document()
        self.document("queued")
        app.KNOWLEDGE_SYNC_RUNNING.set()
        app.knowledge_progress("scan", "正在调用模型生成摘要", api="Responses API", pages="第1-20页")
        files = {f["id"]: f for f in app.knowledge_snapshot()["files"]}
        self.assertEqual(files["scan"]["currentApi"], "Responses API")
        self.assertIn("第1-20页", files["scan"]["statusDetail"])
        self.assertEqual(files["queued"]["taskStatus"], "排队等待分块识别")
        self.assertEqual(files["queued"]["currentApi"], "")
        app.KNOWLEDGE_SYNC_RUNNING.clear()
        self.assertEqual(app.knowledge_snapshot()["files"][0]["currentApi"], "")

    def test_curriculum_chunks_first_and_quota_error_stops_the_batch(self):
        self.document("textbook")
        self.document("课程标准")
        chunk_path = app.UPLOAD_DIR / "temporary-chunk.pdf"
        chunk_path.write_bytes(b"fixture")
        app.KNOWLEDGE_SYNC_RUNNING.set()
        def check_summary_call(*args):
            with app.KNOWLEDGE_PROGRESS_LOCK:
                self.assertEqual(app.KNOWLEDGE_PROGRESS["课程标准"]["api"], "Responses API")
            raise urllib.error.HTTPError("https://example.test", 429, "quota", None, None)
        with mock.patch("pypdf.PdfReader") as reader, \
             mock.patch.object(app, "create_pdf_chunk", return_value=chunk_path), \
             mock.patch.object(app, "ark_upload_document", return_value={"id": "test"}), \
             mock.patch.object(app, "ark_file_status", return_value={"status": "active"}), \
             mock.patch.object(app, "summarize_ark_chunk", side_effect=check_summary_call) as summarize:
            reader.return_value.pages = [None] * 60
            app.sync_ark_knowledge_chunks()
        self.assertEqual(summarize.call_count, 1)
        self.assertEqual(summarize.call_args.args[1], "课程标准.pdf")
        self.assertEqual(app.KNOWLEDGE_PROGRESS, {})


if __name__ == "__main__":
    unittest.main()
