"""Source packaging privacy checks, no real credentials or application state."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import package_release as release


class PackagingTests(unittest.TestCase):
    def test_secret_detection_never_returns_secret(self):
        secret = 'ghp_' + 'a' * 36
        findings = release.scan([('sample.txt', ('prefix\n' + secret).encode())])
        self.assertEqual(findings, [{'file':'sample.txt','line':2,'rule':'provider-token'}])
        self.assertNotIn(secret, str(findings))

    def test_private_key_and_url_password_detection(self):
        private = '-----BEGIN ' + 'PRIVATE KEY-----'
        self.assertEqual(release.scan([('a.txt', private.encode())])[0]['rule'], 'private-key')
        password_url = 'https://' + 'user:longpassword@example.invalid'
        self.assertEqual(release.scan([('a.txt', password_url.encode())])[0]['rule'], 'embedded-url-password')

    def test_local_state_is_not_selected(self):
        cache = release.checked(release.ROOT / '.cache')
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as temp:
            root = Path(temp)
            for name in ['README.md', 'backend/app/main.py', 'backend/app/seller_extension_schemas.json', 'backend/.env',
                    'data/secret.db', '.cache/history/a.bin', 'frontend/node_modules/bad.js']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic')
            (root/'scripts').mkdir()
            with patch.object(release,'ROOT',root), patch.object(release,'FILES',{'README.md'}):
                names = {p.relative_to(root).as_posix() for p in release.sources()}
            self.assertEqual(names, {'README.md','backend/app/main.py','backend/app/seller_extension_schemas.json'})

    def test_external_path_is_rejected(self):
        for path in (release.ROOT.parent / 'outside.txt', release.ROOT / '..' / 'outside.txt'):
            with self.assertRaises(ValueError):
                release.checked(path)


if __name__ == '__main__':
    unittest.main()
