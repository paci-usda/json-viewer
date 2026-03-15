import io
import unittest

import app as viewer_app


class JsonViewerTestCase(unittest.TestCase):
    def setUp(self):
        viewer_app.app.config.update(TESTING=True)
        viewer_app._records = []
        viewer_app._all_fields = []
        viewer_app._included_fields = []
        viewer_app._display_mode = "columns"
        viewer_app._sort_keys = []
        viewer_app._filters = {}
        viewer_app._truncate_limits = {}
        viewer_app._uploaded_files = {}
        self.client = viewer_app.app.test_client()

    def test_multi_file_upload_warns_on_repeat_upload(self):
        response = self.client.post(
            "/",
            data={
                "action": "load",
                "load_mode": "replace",
                "files": [
                    (io.BytesIO(b'{"name":"one"}\n'), "one.jsonl"),
                    (io.BytesIO(b'{"name":"two"}\n'), "two.jsonl"),
                ],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Loaded 2 record(s). 2 total.", response.data)

        duplicate = self.client.post(
            "/",
            data={
                "action": "load",
                "load_mode": "add",
                "files": [(io.BytesIO(b'{"name":"one"}\n'), "one.jsonl")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(b"Already uploaded before: one.jsonl", duplicate.data)
        self.assertIn(b"Loaded 1 record(s). 3 total.", duplicate.data)

    def test_download_formats_export_displayed_records(self):
        self.client.post(
            "/",
            data={
                "action": "load",
                "load_mode": "replace",
                "json_text": '{"name":"Alpha","msg":"hello"}\n{"name":"Beta","msg":"world"}',
            },
            follow_redirects=True,
        )

        expectations = {
            "csv": ("text/csv", b"name,msg", b"Alpha,hello"),
            "tsv": ("text/tab-separated-values", b"name\tmsg", b"Alpha\thello"),
            "json": ("application/json", b'"name": "Alpha"', b'"msg": "world"'),
        }

        for export_format, (content_type, header_row, sample_row) in expectations.items():
            with self.subTest(export_format=export_format):
                response = self.client.post(
                    "/",
                    data={"action": "download", "export_format": export_format},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(content_type, response.headers["Content-Type"])
                self.assertIn("attachment; filename=\"json-viewer-export." + export_format, response.headers["Content-Disposition"])
                self.assertIn(header_row, response.data)
                self.assertIn(sample_row, response.data)

    def test_sort_and_truncate_state_are_reflected_in_ui(self):
        self.client.post(
            "/",
            data={
                "action": "load",
                "load_mode": "replace",
                "json_text": '{"name":"Alpha","msg":"alphabet soup"}\n{"name":"Beta","msg":"beta test"}',
            },
            follow_redirects=True,
        )

        self.client.post(
            "/",
            data={"action": "sort", "sort_field": "name", "sort_order": "desc"},
            follow_redirects=True,
        )
        response = self.client.post(
            "/",
            data={"action": "truncate", "truncate_field": "msg", "truncate_limit": "5"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<option value="name" selected>', response.data)
        self.assertIn(b'name \xe2\x86\x93', response.data)
        self.assertIn(b'Active truncation', response.data)
        self.assertIn(b'msg \xe2\x80\xa65', response.data)
        self.assertIn(b'Truncate to 50', response.data)
        self.assertIn(b'alpha\xe2\x80\xa6', response.data)
        self.assertLess(response.data.index(b'Beta'), response.data.index(b'Alpha'))


if __name__ == "__main__":
    unittest.main()
