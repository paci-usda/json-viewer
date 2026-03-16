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
        viewer_app._column_widths = {}
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
                self.assertIn(
                    f'attachment; filename="json-viewer-export.{export_format}',
                    response.headers["Content-Disposition"],
                )
                self.assertIn(header_row, response.data)
                self.assertIn(sample_row, response.data)

    def test_field_settings_update_width_and_truncate_ui_state(self):
        self.client.post(
            "/",
            data={
                "action": "load",
                "load_mode": "replace",
                "json_text": '{"name":"Alpha","msg":"alphabet soup and a very long value that should wrap after a configured width"}\n{"name":"Beta","msg":"beta test"}',
            },
            follow_redirects=True,
        )

        response = self.client.post(
            "/",
            data={
                "action": "fields",
                "field_order": "name,msg",
                "field_check": ["name", "msg"],
                "field_width_name": "",
                "field_truncate_name": "",
                "field_width_msg": "20",
                "field_truncate_msg": "5",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Active truncation', response.data)
        self.assertIn(b'Active widths', response.data)
        self.assertIn(b'msg \xe2\x80\xa65', response.data)
        self.assertIn(b'msg 20ch', response.data)
        self.assertIn(
            f"Truncate to {viewer_app._default_truncate_limit}".encode(),
            response.data,
        )
        self.assertIn(b'name="field_width_msg" value="20"', response.data)
        self.assertIn(b'name="field_truncate_msg" value="5"', response.data)
        self.assertIn(b'title="Column actions"', response.data)
        self.assertIn(b'class="header-menu-toggle"', response.data)
        self.assertIn(b'width:100ch;max-width:100ch', response.data)
        self.assertIn(b'width:20ch;max-width:20ch', response.data)
        self.assertIn(
            b'>alpha\xe2\x80\xa6</td>',
            response.data,
        )

    def test_invalid_field_settings_show_validation_errors(self):
        self.client.post(
            "/",
            data={
                "action": "load",
                "load_mode": "replace",
                "json_text": '{"name":"Alpha","msg":"alphabet soup"}',
            },
            follow_redirects=True,
        )

        response = self.client.post(
            "/",
            data={
                "action": "fields",
                "field_order": "name,msg",
                "field_check": ["name", "msg"],
                "field_width_name": "0",
                "field_truncate_name": "abc",
                "field_width_msg": "",
                "field_truncate_msg": "",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"Truncate Display values must be positive whole numbers: name",
            response.data,
        )
        self.assertIn(
            b"Width values must be positive whole numbers: name",
            response.data,
        )


if __name__ == "__main__":
    unittest.main()
