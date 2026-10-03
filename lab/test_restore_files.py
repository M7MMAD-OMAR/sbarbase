import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from restore_files import FilesError, admit_tar


ENVIRONMENT = 'e_' + 'a' * 24


def members(*extra):
    root = tarfile.TarInfo(ENVIRONMENT)
    root.type = tarfile.DIRTYPE
    return [root, *extra]


def check(entries):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'objects.tar'
        with tarfile.open(path, 'w') as archive:
            for item in entries:
                archive.addfile(item, io.BytesIO(b'x' * item.size) if item.isfile() else None)
        return admit_tar(path, ENVIRONMENT)


class RestoreObjectArchiveAdmissionTests(unittest.TestCase):
    def test_known_tenant_regular_bytes_and_empty_root_admit(self):
        item = tarfile.TarInfo(ENVIRONMENT + '/bucket/name.txt')
        item.size = 8
        self.assertTrue(check(members(item)))
        self.assertTrue(check(members()))

    def test_foreign_absolute_and_traversal_paths_refuse(self):
        for name in ('/tmp/foreign', 'other/file', ENVIRONMENT + '/../foreign', ENVIRONMENT + '/./file', ENVIRONMENT + '//file'):
            with self.subTest(path=name), self.assertRaises(FilesError):
                check(members(tarfile.TarInfo(name)))

    def test_links_devices_privileged_modes_and_duplicate_entries_refuse(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE):
            item = tarfile.TarInfo(ENVIRONMENT + '/file')
            item.type = kind
            item.linkname = '/foreign'
            with self.subTest(kind=kind), self.assertRaises(FilesError):
                check(members(item))
        item = tarfile.TarInfo(ENVIRONMENT + '/file')
        item.mode = 0o4755
        with self.assertRaises(FilesError):
            check(members(item))
        item.mode = 0o644
        with self.assertRaises(FilesError):
            check(members(item, item))

    def test_missing_directory_root_refuses(self):
        with self.assertRaises(FilesError):
            check([tarfile.TarInfo(ENVIRONMENT + '/file')])
        with self.assertRaises(FilesError):
            check([tarfile.TarInfo(ENVIRONMENT)])


if __name__ == '__main__':
    unittest.main()
