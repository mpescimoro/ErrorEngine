"""
README and docs aligned with the code: settings, links, API endpoints,
routing operators, email template variables, Python versions.
"""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted((ROOT / 'docs').glob('*.md'))
CONFIGURATION = (ROOT / 'docs' / 'configuration.md').read_text(encoding='utf-8')

# Read by the code but not meant for users: development, tests, internal database URL
NOT_DOCUMENTED = {'DATABASE_URL', 'FLASK_ENV', 'FLASK_DEBUG', 'PATH', 'EE_SECRET_MARKER'}
# Only for scripts/check_ibmi.py, documented in docs/ibmi-driver.md
IBMI_SCRIPT = {'IBMI_HOST', 'IBMI_USER', 'IBMI_PASSWORD', 'IBMI_LIBRARY'}


def variables_read_by_the_code():
    found = set()
    for path in ROOT.rglob('*.py'):
        if any(part in ('tests', 'venv', '.venv') for part in path.relative_to(ROOT).parts):
            continue
        source = path.read_text(encoding='utf-8')
        found |= set(re.findall(r"(?:environ\.get|getenv|environ\[)\(?\s*['\"]([A-Z_]{3,})['\"]", source))
    return found


def test_every_setting_is_documented():
    documented = set(re.findall(r'^\| `([A-Z_]+)`(?:, `([A-Z_]+)`)?', CONFIGURATION, re.M))
    documented = {name for pair in documented for name in pair if name}
    example = set(re.findall(r'^#? ?([A-Z_]{3,})=', (ROOT / '.env.example').read_text(encoding='utf-8'), re.M))
    code = variables_read_by_the_code() - NOT_DOCUMENTED - IBMI_SCRIPT

    assert code - documented == set(), 'read by the code, missing from docs/configuration.md'
    assert documented - code == set(), 'in docs/configuration.md, never read by the code'
    assert code - example == set(), 'read by the code, missing from .env.example'
    assert IBMI_SCRIPT <= set(re.findall(r'`(IBMI_[A-Z]+)`', (ROOT / 'docs' / 'ibmi-driver.md').read_text(encoding='utf-8')))


def test_log_level_from_env_is_applied():
    env = dict(os.environ, LOG_LEVEL='warning')
    out = subprocess.run([sys.executable, '-c', 'import serve, logging; print(logging.getLogger().level)'],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().splitlines()[-1] == '30', out.stderr


def test_links_point_to_existing_files_and_headings():
    for page in [ROOT / 'README.md'] + DOCS:
        text = page.read_text(encoding='utf-8')
        for target in re.findall(r'\]\(([^)\s]+)\)', text):
            if target.startswith(('http://', 'https://', 'mailto:')):
                continue
            path, _, anchor = target.partition('#')
            file = (page.parent / path).resolve() if path else page
            assert file.exists(), f'{page.name}: {target}'
            if anchor and file.suffix == '.md':
                headings = re.findall(r'^#+ (.+)$', file.read_text(encoding='utf-8'), re.M)
                slugs = {re.sub(r'[^a-z0-9 -]', '', h.lower()).strip().replace(' ', '-') for h in headings}
                assert anchor in slugs, f'{page.name}: {target}'


def test_api_reference_lists_existing_endpoints(app):
    rules = {}
    for rule in app.url_map.iter_rules():
        path = re.sub(r'<(?:\w+:)?\w+>', '{id}', rule.rule)
        rules.setdefault(path, set()).update(rule.methods)
    reference = (ROOT / 'docs' / 'api-reference.md').read_text(encoding='utf-8')
    rows = re.findall(r'^\| (GET|POST|PUT|DELETE) \| `([^`?]+)', reference, re.M)
    assert len(rows) >= 10
    for method, path in rows:
        assert method in rules.get(path, set()), f'{method} {path}'


def test_routing_operators_match_the_code():
    from routing_service import OPERATORS
    doc = (ROOT / 'docs' / 'routing-rules.md').read_text(encoding='utf-8')
    documented = re.findall(r'^\| `(\w+)` \|', doc, re.M)
    assert sorted(documented) == sorted(OPERATORS)
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert f'{len(OPERATORS)} operators' in readme


def test_email_template_variables_match_the_code():
    tree = ast.parse((ROOT / 'email_service.py').read_text(encoding='utf-8'))
    context = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'context' for t in node.targets)
                   and isinstance(node.value, ast.Call) and getattr(node.value.func, 'id', '') == 'dict')
    in_code = {kw.arg for kw in context.keywords}
    doc = (ROOT / 'docs' / 'templates.md').read_text(encoding='utf-8')
    table = doc[doc.index('## Variables'):doc.index('## Subject')]
    assert set(re.findall(r'^\| `(\w+)` \|', table, re.M)) == in_code


def test_python_versions_match_the_ci():
    ci = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
    versions = re.findall(r'"(3\.\d+)"', ci[ci.index('matrix'):ci.index('steps')])
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert f'Python {versions[0]} to {versions[-1]}' in readme
    for script in ('start.bat', 'start.sh'):
        assert f'Python {versions[0]}+' in (ROOT / script).read_text(encoding='utf-8')


def test_docs_use_the_interface_labels():
    """Buttons and menus named in the docs exist in the templates."""
    templates = ' '.join(p.read_text(encoding='utf-8') for p in (ROOT / 'templates').glob('*.html'))
    templates += (ROOT / 'static' / 'js' / 'app.js').read_text(encoding='utf-8')
    for page in [ROOT / 'README.md'] + DOCS:
        for label in re.findall(r'\*\*([A-Z][A-Za-z ]+?)\*\*', page.read_text(encoding='utf-8')):
            if label in ('Required', 'Log', 'Network access', 'External monitoring', 'Backup',
                         'Requirements', 'Windows', 'Linux', 'Databases and APIs', 'Notifications',
                         'Conditional routing', 'Scheduling', 'Reliable delivery', 'Self', 'Self-hosted',
                         'Windows service or scheduled task', 'Java', 'JPype', 'jt400', 'ibm_db', 'pyodbc'):
                continue
            assert label in templates, f'{page.name}: **{label}**'
