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

    def test_resolve_column_widths_uses_displayed_values_and_caps_defaults(self):
        widths = viewer_app._resolve_column_widths(
            [
                {"short": "abcd", "wide": "x" * 130, "msg": "alphabet soup"},
                {"short": "abc", "wide": "wide", "msg": "beta"},
            ],
            ["short", "wide", "msg", "explicit"],
            {"msg": 5},
            {"explicit": 12},
        )

        self.assertEqual(widths["short"], 5)
        self.assertEqual(widths["wide"], 100)
        self.assertEqual(widths["msg"], 6)
        self.assertEqual(widths["explicit"], 12)

    def test_compute_column_statistics_tracks_raw_and_displayed_values(self):
        stats = viewer_app._compute_column_statistics(
            [
                {"msg": "alphabet soup"},
                {"msg": "alphabet salad"},
                {"msg": "beta"},
            ],
            "msg",
            5,
        )

        self.assertEqual(stats["unique_count"], 3)
        self.assertEqual(stats["displayed_unique_count"], 2)
        self.assertEqual(stats["min_value"], "alphabet salad")
        self.assertEqual(stats["max_value"], "beta")
        self.assertEqual(stats["min_length"], 4)
        self.assertEqual(stats["max_length"], 14)

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
                "json_text": '{"name":"Alpha","msg":"alphabet soup and a very long value that should wrap after a configured width","wide":"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}\n{"name":"Beta","msg":"alphabet salad","wide":"short"}',
            },
            follow_redirects=True,
        )

        response = self.client.post(
            "/",
            data={
                "action": "fields",
                "field_order": "name,msg,wide",
                "field_check": ["name", "msg", "wide"],
                "field_width_name": "",
                "field_truncate_name": "",
                "field_width_msg": "20",
                "field_truncate_msg": "5",
                "field_width_wide": "",
                "field_truncate_wide": "",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-drawer="data">Data</button>', response.data)
        self.assertIn(b'data-drawer="sort">Sort</button>', response.data)
        self.assertNotIn(b'View &amp; Sort', response.data)
        self.assertIn(b'Download displayed data as:', response.data)
        self.assertIn(b'id="display-mode-select"', response.data)
        self.assertIn(b'data-auto-submit="1"', response.data)
        self.assertIn(b'class="field-control-inline"', response.data)
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
        self.assertIn(b'Unique values: 2', response.data)
        self.assertIn(b'Unique displayed values: 1', response.data)
        self.assertIn(b'Statistics for msg', response.data)
        self.assertIn(b'width:5ch;min-width:5ch;max-width:5ch', response.data)
        self.assertIn(b'width:20ch;min-width:20ch;max-width:20ch', response.data)
        self.assertIn(b'width:100ch;min-width:100ch;max-width:100ch', response.data)
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
