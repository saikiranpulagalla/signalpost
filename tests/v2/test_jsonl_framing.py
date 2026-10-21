"""Physical-LF framing is independent of valid Unicode inside JSON strings."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v0_kernel.storage import read_jsonl, read_jsonl_bytes, iter_jsonl, atomic_jsonl


class JsonlFraming(unittest.TestCase):
    def setUp(self):
        self.temp_root=Path(__file__).resolve().parents[2]/'.release-gate-tmp'
        self.temp_root.mkdir(exist_ok=True)
    def test_literal_unicode_separators_are_not_record_boundaries(self):
        rows = [{'value': 'Ø\u2028\u2029\u0085Å'}, {'value': 'unchanged'}]
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            path = Path(directory)/'values.jsonl'
            path.write_bytes(('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows)+'\n').encode('utf-8'))
            self.assertEqual(read_jsonl(path), rows)

    def test_literal_escaped_positions_and_physical_endings_are_equivalent(self):
        for separator in ('\u2028', '\u2029', '\u0085', '\u2028\u2029\u0085'):
            for value in (separator+'Ø', 'Ø'+separator+'Å', 'Ø'+separator, separator*3+' " \\ Æ'):
                rows=[{'value':value},{'value':'escaped\nnewline\tand\rreturn'}]
                for ascii_only in (False,True):
                    for ending in ('\n','\r\n'):
                        for final in ('',ending):
                            raw=(ending.join(json.dumps(x,ensure_ascii=ascii_only) for x in rows)+final).encode()
                            with self.subTest(value=repr(value),ending=repr(ending),final=bool(final),escaped=ascii_only):
                                self.assertEqual(read_jsonl_bytes(raw),rows)

    def test_blanks_trailing_newline_and_atomic_writer_preserve_values(self):
        rows=[{'value':'\u2028\u2029\u0085'}]
        self.assertEqual(read_jsonl_bytes(b' \t\r\n\n'+json.dumps(rows[0],ensure_ascii=False).encode()+b'\r\n\t\n'),rows)
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            path=Path(directory)/'values.jsonl';atomic_jsonl(path,rows)
            original=path.read_bytes();self.assertIn('\u2028'.encode(),original)
            self.assertEqual(read_jsonl(path),rows);self.assertEqual(path.read_bytes(),original)

    def test_malformed_utf8_json_multiline_truncation_and_cr_only_are_rejected(self):
        for raw in (b'\xff\n',b'{"a":\n1}\n',b'{"a":1',b'{}\r{}\r',b'{}\r',b'{bad}\n',b'"literal\nnewline"\n', '\u2028\n'.encode()):
            with self.subTest(raw=repr(raw)),self.assertRaises((ValueError,UnicodeError)):
                read_jsonl_bytes(raw)

    def test_shapes_and_explicit_bom_compatibility(self):
        self.assertEqual(read_jsonl_bytes(b'"923609016"\n'),['923609016'])
        for raw in (b'[]\n',b'null\n',b'"string"\n'):
            with self.assertRaises(ValueError):read_jsonl_bytes(raw,require_object=True)
        with self.assertRaises(ValueError):read_jsonl_bytes(b'\xef\xbb\xbf{}\n')
        self.assertEqual(read_jsonl_bytes(b'\xef\xbb\xbf{}\n',encoding='utf-8-sig'),[{}])
        with self.assertRaises(ValueError):read_jsonl_bytes(b'{}\n\xef\xbb\xbf{}\n',encoding='utf-8-sig')

    def test_path_reader_is_streaming_and_strictly_utf8(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as directory:
            path=Path(directory)/'stream.jsonl';path.write_bytes(b'{"first":1}\n{broken}\n')
            with patch.object(Path,'read_text',side_effect=AssertionError('whole-file text read')),patch.object(Path,'read_bytes',side_effect=AssertionError('whole-file byte read')):
                values=iter_jsonl(path);self.assertEqual(next(values),{'first':1})
                with self.assertRaises(ValueError):next(values)
        with self.assertRaises(ValueError):read_jsonl_bytes(b'{}',encoding='latin1')


if __name__ == '__main__':
    unittest.main()
