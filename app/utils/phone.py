import re

_BD_MOBILE = re.compile(r"^(?:\+?88)?(01[3-9]\d{8})$")


def normalize_phone(value):
    """Normalize Bangladeshi mobile numbers to +8801XXXXXXXXX.

    Accepts `01XXXXXXXXX`, `8801XXXXXXXXX` and `+8801XXXXXXXXX` (spaces and
    dashes are ignored). Numbers that are not Bangladeshi mobiles are returned
    with separators stripped, so foreign numbers still work.
    """
    if not value:
        return ""
    compact = re.sub(r"[\s\-().]", "", str(value).strip())
    match = _BD_MOBILE.match(compact)
    if match:
        return f"+88{match.group(1)}"
    return compact
