import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

DEPLOY=Path(__file__).resolve().parents[1]/'deploy'
sys.path.insert(0,str(DEPLOY))
sys.path.insert(0,str(DEPLOY.parent/'src'))
spec=importlib.util.spec_from_file_location('jer_installer',DEPLOY/'install.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_update_preserves_operator_configuration(tmp_path):
    file=tmp_path/'config.json'
    original=dict(passive_port='/dev/serial/by-path/usb-1',host='192.168.1.10',port=9110,stale_s=20)
    file.write_text(json.dumps(original))
    args=SimpleNamespace(port=None,listen=None,metrics_port=None)
    assert installer.config_for(file,args)==original
    args.listen='127.0.0.1'
    updated=installer.config_for(file,args)
    assert updated==original|dict(host='127.0.0.1')
    installer.write_config(file,updated)
    assert json.loads(file.read_text())==updated


def test_reject_first_install_without_port(tmp_path):
    with pytest.raises(ValueError,match='passive_port'):
        installer.config_for(tmp_path/'absent',SimpleNamespace(port=None,listen=None,metrics_port=None))


@pytest.mark.parametrize('name,expected',[('bookworm',True),('trixie',True),('bullseye',False),('buster',False)])
def test_os_baseline(name,expected):
    assert installer.os_supported(dict(ID='raspbian',VERSION_CODENAME=name))==expected


@pytest.mark.skipif(os.name=='nt',reason='POSIX symlink deployment runs on Linux CI')
def test_atomic_release_switch_retains_old_release(tmp_path):
    first=tmp_path/'first';first.mkdir();(first/'data').write_text('old')
    second=tmp_path/'second';second.mkdir()
    link=tmp_path/'current'
    installer.replace_link(link,first)
    installer.replace_link(link,second)
    assert link.resolve()==second and (first/'data').read_text()=='old'
    with pytest.raises(ValueError):installer.replace_link(first,second)


@pytest.mark.skipif(os.name=='nt',reason='POSIX installation lifecycle runs on Linux CI')
@pytest.mark.parametrize('health_fails',[False,True])
def test_managed_update_and_rollback(tmp_path,monkeypatch,health_fails):
    base=tmp_path/'opt';base.mkdir()
    old=base/'old';old.mkdir()
    (base/'current').symlink_to(old,target_is_directory=True)
    config=tmp_path/'etc/config.json';config.parent.mkdir()
    original=dict(passive_port='/dev/ttyACM0',host='127.0.0.1',port=9109,stale_s=20)
    config.write_text(json.dumps(original))
    unit=tmp_path/'old.service';unit.write_text('old unit')
    monkeypatch.setattr(installer,'BASE',base)
    monkeypatch.setattr(installer,'CONFIG',config)
    monkeypatch.setattr(installer,'UNIT',unit)
    monkeypatch.setattr(installer.platform,'system',lambda:'Linux')
    monkeypatch.setattr(installer.platform,'freedesktop_os_release',lambda:dict(ID='raspbian',VERSION_CODENAME='trixie'))
    monkeypatch.setattr(installer.os,'geteuid',lambda:0)
    monkeypatch.setattr(installer.os,'umask',lambda _:None)
    exists=Path.exists
    monkeypatch.setattr(Path,'exists',lambda p:True if str(p)=='/run/systemd/system' else exists(p))
    monkeypatch.setitem(sys.modules,'grp',SimpleNamespace(getgrnam=lambda _:SimpleNamespace(gr_gid=1001)))
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=lambda _:SimpleNamespace(pw_uid=1001)))
    commands=[]
    monkeypatch.setattr(installer,'run',lambda *cmd,**kw:commands.append(cmd))
    monkeypatch.setattr(installer.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0))
    def health(config):
        if health_fails:raise RuntimeError('simulated failure')
    monkeypatch.setattr(installer,'check_health',health)
    if health_fails:
        with pytest.raises(RuntimeError):installer.main(['--skip-apt'])
        assert (base/'current').resolve()==old
        assert unit.read_text()=='old unit'
    else:
        installer.main(['--skip-apt'])
        assert (base/'current').resolve()!=old
        assert (base/'previous').resolve()==old
        assert (base/'current/src/jer_usb/service.py').exists()
    assert json.loads(config.read_text())==original
    assert ('systemctl','restart','jer-usb.service') in commands


def test_deployment_bundle_excludes_private_data(tmp_path):
    import tarfile
    spec=importlib.util.spec_from_file_location('bundle_builder',DEPLOY.parent/'tools/build_deploy_bundle.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    import shutil
    root=tmp_path/'project';root.mkdir()
    for name in ('src','deploy'):
        shutil.copytree(DEPLOY.parent/name,root/name)
    for name in ('pyproject.toml','INSTALL_RASPBERRY_PI.md','RASPBERRY_PI_COMPATIBILITY.md'):
        shutil.copyfile(DEPLOY.parent/name,root/name)
    (root/'log').mkdir();(root/'log/private.pcapng').write_text('private')
    archive=module.build(root)
    with tarfile.open(archive) as bundle:
        names=bundle.getnames()
        assert not any('/log/' in s or '__pycache__' in s or '/reports/' in s for s in names)
        assert 'jer-usb-logger/deploy/install.sh' in names
        assert b'\r' not in bundle.extractfile('jer-usb-logger/deploy/install.sh').read()
