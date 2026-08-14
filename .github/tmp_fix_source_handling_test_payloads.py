from pathlib import Path

path = Path('tests/test_source_handling_authority_enforcement.py')
text = path.read_text()

fact = '''{
        "sensitivity": "PUBLIC",
        "operation_restrictions": [],
        "persistence_restriction": "FULL_CONTENT_ALLOWED",
        "secret_presence": [],
        "operation_restrictions_known": True,
        "secret_presence_known": True,
        "withdrawn": False,
        "deleted_at_source": False,
        "historically_unavailable": False,
        "availability_known": True,
    }'''

old = '    authorized_payload = {"scope": "doc-1", "value": "authorized", **_times()}\n'
new = f'    authorized_payload = {{"scope": "doc-1", "fact": {fact}, **_times()}}\n'
if old not in text:
    raise SystemExit('authorized payload anchor missing')
text = text.replace(old, new, 1)

old = '''                "scope": "doc-1",
                "value": "tampered",
                **_times(),
'''
new = f'''                "scope": "doc-1",
                "fact": {{
                    **authorized_payload["fact"],
                    "sensitivity": "INTERNAL",
                }},
                **_times(),
'''
if old not in text:
    raise SystemExit('tampered payload anchor missing')
text = text.replace(old, new, 1)

old = '    payload = {"scope": "doc-1", "value": "authorized", **_times()}\n'
new = f'    payload = {{"scope": "doc-1", "fact": {fact}, **_times()}}\n'
if old not in text:
    raise SystemExit('counterfactual payload anchor missing')
text = text.replace(old, new, 1)

path.write_text(text)
