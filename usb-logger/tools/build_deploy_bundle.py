"""Build a small source deployment bundle without private captures or dependencies."""
import hashlib
import tarfile
import tomllib
from pathlib import Path


def build(root=None):
    root = root or Path(__file__).resolve().parents[1]
    version = tomllib.loads((root/'pyproject.toml').read_text())['project']['version']
    output = root/'dist'/f'jer-usb-logger-{version}.tar.gz'
    output.parent.mkdir(exist_ok=True)
    files = list((root/'src/jer_usb').glob('*.py'))
    files += [root/'deploy'/name for name in
              ('install.sh','install.py','doctor.py','jer-usb.service','config.example.json','prometheus.yaml')]
    files += [root/name for name in ('pyproject.toml','INSTALL_RASPBERRY_PI.md','RASPBERRY_PI_COMPATIBILITY.md')]
    with tarfile.open(output, 'w:gz') as bundle:
        for path in sorted(files):
            bundle.add(path, arcname='jer-usb-logger/'+path.relative_to(root).as_posix(), recursive=False)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(f'{digest}  {output.name}\n')
    print(output)
    print('SHA256:',digest)
    return output


if __name__=='__main__':
    build()
