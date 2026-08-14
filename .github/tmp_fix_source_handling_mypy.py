from pathlib import Path

path = Path("tests/test_source_handling_authority_enforcement.py")
text = path.read_text()
old = '''                "fact": {
                    **authorized_payload["fact"],
                    "sensitivity": "INTERNAL",
                },
'''
new = '''                "fact": {
                    "sensitivity": "INTERNAL",
                    "operation_restrictions": [],
                    "persistence_restriction": "FULL_CONTENT_ALLOWED",
                    "secret_presence": [],
                    "operation_restrictions_known": True,
                    "secret_presence_known": True,
                    "withdrawn": False,
                    "deleted_at_source": False,
                    "historically_unavailable": False,
                    "availability_known": True,
                },
'''
if old not in text:
    raise SystemExit("typed fixture anchor missing")
path.write_text(text.replace(old, new, 1))
