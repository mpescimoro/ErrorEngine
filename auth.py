r"""
Single-user login, configured in .env:

    ERRORENGINE_USER            username (default: admin)
    ERRORENGINE_PASSWORD_HASH   password hash, generated with:
        venv\Scripts\python.exe -c "import getpass; from werkzeug.security import generate_password_hash as h; print(h(getpass.getpass('Password: ')))"
    ERRORENGINE_API_TOKEN       optional: to call the APIs from scripts, with the header
                                "Authorization: Bearer <token>"
    ERRORENGINE_SESSION_HOURS   hours of inactivity after which the session expires (default 12)

Every page and API requires login, except the login page, static files
and /api/health (for external checks). Until the password hash and a SECRET_KEY
other than the example one are set, nobody can open the interface; the scheduler
keeps running. Without any SECRET_KEY, serve.py does not start at all.
Changing the password or the username ends the open sessions.

The forms and the requests of the pages that change something carry a token of the
session (CSRF), checked here: another site cannot make a signed-in browser send them.
Scripts with the API token do not need it.
"""
import hashlib
import hmac
import logging
import math
import secrets
import threading
import time
from urllib.parse import urlsplit

from flask import (Blueprint, current_app, flash, has_request_context, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash

from config import INSECURE_SECRET_KEYS

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

# Endpoints reachable without login
PUBLIC_ENDPOINTS = {'auth.login', 'static', 'api.api_health'}
# Methods of the requests that change something: they need the CSRF token
UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
# SECRET_KEY and the API token: shorter ones are too easy to guess
MIN_KEY_LENGTH = 32
# After LOGIN_ATTEMPTS wrong passwords, an address waits LOGIN_BLOCK_SECONDS before the next try; failures
# older than LOGIN_FORGET_SECONDS no longer count. Short waits: without ERRORENGINE_TRUSTED_PROXY every
# attempt seems to come from the reverse proxy, the administrator's included
LOGIN_ATTEMPTS = 5
LOGIN_BLOCK_SECONDS = 60
LOGIN_FORGET_SECONDS = 15 * 60
_failed_logins_lock = threading.Lock()


def init_auth(app):
    """Registers login/logout and the checks on every request."""
    app.register_blueprint(auth_bp)
    app.before_request(_require_login)
    app.before_request(_check_csrf)
    app.jinja_env.globals['csrf_token'] = csrf_token
    # Wrong passwords by address: (how many in a row, time of the last one). In memory, a single process
    app.extensions['failed_logins'] = {}

    @app.context_processor
    def inject_current_user():
        # Emails also go through the context processors, but the scheduler creates
        # them outside a request: there is no session there
        return {'current_user': session.get('user') if has_request_context() else None}

    if not app.config.get('LOGIN_DISABLED'):
        problem = _configuration_problem(app.config)
        if problem:
            logger.warning(f"Login not configured. {problem} "
                           "Until it is fixed, nobody can open the interface.")
    token = app.config.get('API_TOKEN')
    if token and len(token) < MIN_KEY_LENGTH:
        logger.warning(f"ERRORENGINE_API_TOKEN is shorter than {MIN_KEY_LENGTH} characters: scripts that use it "
                       "are refused. Generate a longer one, as for SECRET_KEY")


def _configuration_problem(config) -> str:
    """What is missing to use the login (empty string when everything is fine)."""
    if not config.get('LOGIN_PASSWORD_HASH'):
        return "ERRORENGINE_PASSWORD_HASH is missing from the server's .env file."
    if not config.get('SECRET_KEY') or config.get('SECRET_KEY') in INSECURE_SECRET_KEYS:
        return "The SECRET_KEY in the server's .env file is missing or is the example one."
    if len(config['SECRET_KEY']) < MIN_KEY_LENGTH:
        return f"The SECRET_KEY in the server's .env file is shorter than {MIN_KEY_LENGTH} characters."
    return ''


def _credentials_fingerprint() -> str:
    """Fingerprint of username and password: when they change, open sessions are no longer valid."""
    cfg = current_app.config
    data = f"{cfg['LOGIN_USERNAME']}:{cfg['LOGIN_PASSWORD_HASH']}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def _is_logged_in() -> bool:
    return bool(session.get('user')) and hmac.compare_digest(
        session.get('auth', ''), _credentials_fingerprint())


def _valid_api_token() -> bool:
    token = current_app.config.get('API_TOKEN')
    if not token or len(token) < MIN_KEY_LENGTH:
        return False
    scheme, _, value = request.headers.get('Authorization', '').partition(' ')
    return scheme.lower() == 'bearer' and hmac.compare_digest(value.strip().encode(), token.encode())


def is_authenticated() -> bool:
    """Signed-in user, valid API token, or login turned off (tests)."""
    cfg = current_app.config
    if cfg.get('LOGIN_DISABLED'):
        return True
    return not _configuration_problem(cfg) and (_is_logged_in() or _valid_api_token())


def _require_login():
    """before_request: lets through only signed-in users."""
    if current_app.config.get('LOGIN_DISABLED') or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not _configuration_problem(current_app.config) and _is_logged_in():
        return None
    if request.path.startswith('/api/'):
        if not _configuration_problem(current_app.config) and _valid_api_token():
            return None
        return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
    return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))


def csrf_token() -> str:
    """The CSRF token of this browser session, for the forms and for app.js (meta tag in base.html)."""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def _check_csrf():
    """
    before_request, after the login: a request that changes something must carry the token of
    the session. Scripts with the API token are not a browser with a cookie to borrow.
    """
    if (request.method not in UNSAFE_METHODS or current_app.config.get('CSRF_DISABLED')
            or _valid_api_token()):
        return None
    sent = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token') or ''
    expected = session.get('csrf_token', '')
    # Where the request comes from, as the browser says (over HTTPS and on localhost only)
    elsewhere = request.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none')
    if expected and not elsewhere and hmac.compare_digest(sent.encode(), expected.encode()):
        return None
    logger.warning(f"Refused {request.method} {request.path} from {request.remote_addr}: "
                   "CSRF token missing or wrong")
    if request.path.startswith('/api/'):
        return jsonify({'error': 'csrf', 'message': 'The page is out of date: reload it and try again'}), 403
    flash('The page was out of date and has been reloaded: try again', 'warning')
    # Back to the page with the form, if it is a page of ErrorEngine; otherwise the dashboard.
    # Behind a proxy that changes the host, the browser itself can say the form was ours
    back = urlsplit(request.referrer or '')
    ours = back.netloc == request.host or request.headers.get('Sec-Fetch-Site') == 'same-origin'
    page = back.path + (f'?{back.query}' if back.query else '') if ours else ''
    return redirect(_safe_next(page))


def _client_address() -> str:
    """The address of the client, without the port some proxies add to an IPv6 one ([2001:db8::1]:51234)."""
    address = request.remote_addr or ''
    return address[1:address.index(']')] if address.startswith('[') and ']' in address else address


def _login_attempt(failed: dict, address: str) -> tuple:
    """
    (seconds the address must still wait, number of this try in a row). When it may try, the try is
    counted here, before the password is checked: tries that arrive together cannot all pass before
    the first one fails.
    """
    now = time.monotonic()
    with _failed_logins_lock:
        for other, (_, last) in list(failed.items()):
            if now - last > LOGIN_FORGET_SECONDS:
                del failed[other]
        count, last = failed.get(address, (0, now))
        if count >= LOGIN_ATTEMPTS and now < last + LOGIN_BLOCK_SECONDS:
            return math.ceil(last + LOGIN_BLOCK_SECONDS - now), count
        failed[address] = (count + 1, now)
    return 0, count + 1


def _safe_next(target) -> str:
    """After login, only go back to pages of this site."""
    if (target and target.startswith('/') and not target.startswith('//')
            and '\\' not in target and not urlsplit(target).netloc):
        return target
    return url_for('main.dashboard')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    problem = _configuration_problem(current_app.config)
    next_url = _safe_next(request.values.get('next'))

    if not problem and _is_logged_in():
        return redirect(next_url)

    error, status = None, 200
    if request.method == 'POST' and not problem:
        failed, address = current_app.extensions['failed_logins'], _client_address()
        wait, attempt = _login_attempt(failed, address)
        if wait:
            # The password is not even checked while the address waits
            error = (f"Too many wrong passwords from this address: try again in {wait} "
                     f"{'second' if wait == 1 else 'seconds'}.")
            status = 429
        else:
            cfg = current_app.config
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            try:
                password_ok = check_password_hash(cfg['LOGIN_PASSWORD_HASH'], password)
            except ValueError:
                logger.error("ERRORENGINE_PASSWORD_HASH is not valid: generate it again")
                password_ok = False
            user_ok = hmac.compare_digest(username.encode(), cfg['LOGIN_USERNAME'].encode())

            if user_ok and password_ok:
                with _failed_logins_lock:
                    failed.pop(address, None)
                session.clear()
                session.permanent = True
                session['user'] = username
                session['auth'] = _credentials_fingerprint()
                logger.info(f"Login: {username} from {request.remote_addr}")
                return redirect(next_url)

            logger.warning(f"Failed login for '{username}' from {request.remote_addr}")
            # Logged by the try that starts the wait, not by every one checked together with it
            if attempt >= LOGIN_ATTEMPTS:
                logger.warning(f"Sign-in from {address} paused for {LOGIN_BLOCK_SECONDS} s "
                               f"after {attempt} wrong passwords in a row")
            error = 'Wrong username or password.'

    return render_template('login.html', error=error, problem=problem, next_url=next_url,
                           username=request.form.get('username', '')), status


@auth_bp.route('/logout', methods=['POST'])
def logout():
    user = session.get('user')
    session.clear()
    if user:
        logger.info(f"Logout: {user}")
    return redirect(url_for('auth.login'))
