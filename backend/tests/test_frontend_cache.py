"""A reload must fetch the current HTML and its fingerprinted JS bundle."""
from fastapi.testclient import TestClient
from app.main import create_app
from app.connection import Connection
from app.storage import Store


def test_start_page_is_not_cached_but_static_assets_still_work(tmp_path,monkeypatch):
    monkeypatch.setattr('app.main.ROOT',tmp_path)
    dist=tmp_path/'frontend/dist'
    dist.mkdir(parents=True)
    (dist/'index.html').write_text('<html>synthetic</html>',encoding='utf-8')
    (dist/'app-test.js').write_text('void 0',encoding='utf-8')
    app=create_app(Store(tmp_path/'mock.sqlite3',root=tmp_path),connection=Connection('MOCK'))
    with TestClient(app,base_url='http://localhost') as api:
        response=api.get('/')
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert response.headers['pragma']=='no-cache'
        assert api.get('/app-test.js').status_code==200
        assert api.get('/../backend/secret').status_code==404
