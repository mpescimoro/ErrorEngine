"""
Fonts and icons served by the app: the interface also works on a server without Internet access.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CDN = re.compile(r'https?://[^"\'\s)]*(googleapis|gstatic|jsdelivr|cdnjs|unpkg)', re.I)


def test_no_cdn_resources_in_templates_and_css():
    files = list((ROOT / 'templates').rglob('*.html')) + list((ROOT / 'static').rglob('*.css'))
    found = [f'{f.relative_to(ROOT)}: {m.group(0)}' for f in files
             for m in CDN.finditer(f.read_text(encoding='utf-8'))]
    assert found == []


def test_font_and_icon_files_exist():
    vendor = ROOT / 'static' / 'vendor'
    for css in (vendor / 'fonts' / 'fonts.css', vendor / 'bootstrap-icons' / 'bootstrap-icons.min.css'):
        for url in re.findall(r'url\(["\']?([^"\')?]+)', css.read_text(encoding='utf-8')):
            assert (css.parent / url).is_file(), f'{css.name}: missing {url}'


def test_fonts_and_icons_reachable_without_login(app, client):
    app.config['LOGIN_DISABLED'] = False   # as in production: the login page needs them
    for path in ('/static/vendor/fonts/fonts.css', '/static/vendor/fonts/dm-sans-latin-400-normal.woff2',
                 '/static/vendor/bootstrap-icons/bootstrap-icons.min.css',
                 '/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2'):
        assert client.get(path).status_code == 200, path
