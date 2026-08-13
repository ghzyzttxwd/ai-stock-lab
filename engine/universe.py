from __future__ import annotations


def is_main_board(symbol: str) -> bool:
    """Restrict to Shanghai/Shenzhen main-board A shares.

    Explicitly excludes STAR (688/689), ChiNext (300/301), BSE and B shares.
    """
    s = symbol.lower().strip()
    if s.startswith('sh.'):
        code = s[3:]
        return code.startswith(('600', '601', '603', '605'))
    if s.startswith('sz.'):
        code = s[3:]
        return code.startswith(('000', '001', '002', '003'))
    return False


def eligible_row(row: dict) -> bool:
    if not is_main_board(str(row.get('code', ''))):
        return False
    if str(row.get('tradestatus', '1')) != '1':
        return False
    if str(row.get('isST', '0')) == '1':
        return False
    name = str(row.get('name', '')).upper()
    if 'ST' in name or '退' in name:
        return False
    return True
