"""Closed launcher caching, synthetic content only."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from test_lifecycle import runner,root


def setup(root):
    runner.prepare()
    (root/'frontend/package-lock.json').write_text(json.dumps({'lockfileVersion':3,'packages':{}}))
    (root/'frontend/src').mkdir()
    (root/'frontend/src/main.ts').write_text('export const version=1')
    (root/'frontend/dist').mkdir()
    (root/'frontend/dist/index.html').write_text('synthetic build')
    for name in ('backend/.venv/Scripts/python.exe','frontend/node_modules/vite/bin/vite.js'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic')
    (root/'.cache/lifecycle/dependencies.json').write_text(json.dumps({'fingerprint':runner.dependency_fingerprint()}))


def cache(root):
    fingerprint=runner.build_fingerprint()
    (root/'.cache/lifecycle/build.json').write_text(json.dumps({'source':fingerprint,'dist':runner.tree_fingerprint(root/'frontend/dist')}))
    return fingerprint


def test_unchanged_inputs_and_dist_skip_all_compilers(root,monkeypatch):
    setup(root);cache(root)
    child=Mock(side_effect=AssertionError('No install/compiler for a valid cached build'))
    monkeypatch.setattr(runner,'execute',child)
    runner.install_and_build(runner.environment({}))
    child.assert_not_called()


@pytest.mark.parametrize('name',['src/main.ts','vite.config.ts','public/new.css','package.json','dist/index.html','dist/new.js'])
def test_changed_source_config_public_or_output_invalidates_cache(root,name):
    setup(root);fingerprint=cache(root)
    path=root/'frontend'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('changed synthetic content')
    assert not runner.build_current(runner.build_fingerprint())
    if name.startswith('dist/'):
        assert runner.build_fingerprint()==fingerprint


def test_content_changes_detected_even_if_timestamp_and_size_restored(root):
    import os
    setup(root);fingerprint=cache(root)
    path=root/'frontend/src/main.ts';info=path.stat()
    path.write_text('export const version=2')
    os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
    assert path.stat().st_size==info.st_size
    assert runner.build_fingerprint()!=fingerprint


@pytest.mark.parametrize('corruption',['not-json','{}','[]','"invalid marker"',' '*1025])
def test_bad_marker_rebuilds_and_success_updates_marker(root,monkeypatch,corruption):
    setup(root);(root/'.cache/lifecycle/build.json').write_text(corruption)
    child=Mock();monkeypatch.setattr(runner,'execute',child)
    runner.install_and_build(runner.environment({}))
    assert child.call_count==3
    assert runner.build_current(runner.build_fingerprint())


def test_compiler_failure_cannot_register_new_cache(root,monkeypatch):
    setup(root)
    child=Mock(side_effect=RuntimeError('synthetic compiler failure'))
    monkeypatch.setattr(runner,'execute',child)
    with pytest.raises(RuntimeError):runner.install_and_build(runner.environment({}))
    assert not (root/'.cache/lifecycle/build.json').exists()


def test_source_changed_during_build_not_registered(root,monkeypatch):
    setup(root)
    def change(*args):(root/'frontend/src/main.ts').write_text('changed while building')
    monkeypatch.setattr(runner,'execute',change)
    with pytest.raises(ValueError,match='changed during build'):runner.install_and_build(runner.environment({}))
    assert not (root/'.cache/lifecycle/build.json').exists()


def test_backend_change_does_not_rebuild_frontend(root):
    setup(root);cache(root)
    (root/'backend/changed.py').write_text('synthetic backend change')
    assert runner.build_current(runner.build_fingerprint())


def test_nested_reparse_point_cannot_pass_preflight(root,monkeypatch):
    from test_lifecycle import test_nested_symlink_denied_before_child
    test_nested_symlink_denied_before_child(root,monkeypatch)
