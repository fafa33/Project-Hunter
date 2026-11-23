from pathlib import Path
import pytest
from hunter.automation.macos_opencode_container_sandbox import translate, ContainerSandboxRefused

IMAGE = 'example.invalid/hunter-opencode@sha256:' + 'a'*64

def argv(tmp_path: Path):
    root = tmp_path/'hunter-opencode-attempt-test'
    (root/'repo').mkdir(parents=True)
    (root/'credential-home').mkdir()
    return ['--die-with-parent','--new-session','--unshare-pid','--unshare-ipc','--unshare-uts','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--ro-bind','/usr','/usr','--bind',str(root/'repo'),'/workspace','--bind',str(root/'credential-home'),'/home/hunter','--chdir','/workspace','/usr/local/bin/opencode','run','test']

def test_exact_contract_translates_to_read_only_restricted_container(tmp_path):
    result=translate(argv(tmp_path),IMAGE)
    assert result[0:2]==['docker','run']
    for item in ('--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=128'):
        assert item in result
    assert result[-3:]==['/usr/local/bin/opencode','run','test']

@pytest.mark.parametrize('change', ['image','mount','flag','binary','chdir'])
def test_fail_closed_on_untrusted_configuration(tmp_path,change):
    args=argv(tmp_path); image=IMAGE
    if change=='image':image='hunter:latest'
    if change=='mount':args[args.index('/workspace')]='/'
    if change=='flag':args.remove('--unshare-pid')
    if change=='binary':args[-3]='/bin/sh'
    if change=='chdir':args[args.index('--chdir')+1]='/tmp'
    with pytest.raises(ContainerSandboxRefused):translate(args,image)


def test_mac_host_binary_is_not_mounted_into_container(tmp_path):
    args=argv(tmp_path)
    args[args.index('--chdir'):args.index('--chdir')]=['--dir','/opt/hunter/bin','--ro-bind','/usr/local/bin/opencode','/opt/hunter/bin/opencode']
    args[-3]='/opt/hunter/bin/opencode'
    result=translate(args,IMAGE)
    assert '/opt/hunter/bin/opencode' not in result
    assert result[-3:]==['/usr/local/bin/opencode','run','test']


def test_local_immutable_image_id_is_accepted(tmp_path):
    result = translate(argv(tmp_path), 'sha256:' + 'b'*64)
    assert 'sha256:' + 'b'*64 in result
