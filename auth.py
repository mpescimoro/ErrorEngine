r"""
Single-user login, configured in .env:

    ERRORENGINE_USER            username (default: admin)
    ERRORENGINE_PASSWORD_HASH   password hash, generated with:
        venv\Scripts\python.exe -c "import getpass; from werkzeug.security import generate_password_hash as h; print(h(getpass.getpass('Password: ')))"
    ERRORENGINE_API_TOKEN       optional: to call the APIs from scripts, with the header
                                "Authorization: Bearer <token>". It reads, and changes only
                                what docs/api-reference.md lists (TOKEN_ACTIONS)
    ERRORENGINE_SESSION_HOURS   hours of inactivity after which the session expires (default 12):
                                the requests a page makes by itself do not count

Every page and API requires login, except the login page, static files
and /api/health (for external checks). Until the password hash is set, nobody can
open the interface; the scheduler keeps running.
Changing the password or the username ends the open sessions; so do signing out, in every
browser, and a restart.

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

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

# Endpoints reachable without login
PUBLIC_ENDPOINTS = {'auth.login', 'static', 'api.api_health'}
# Methods of the requests that change something: they need the CSRF token
UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
# What the API token can change, the actions in docs/api-reference.md: it runs and tests what is saved and
# resolves errors. The other requests that change something are those of the pages: only with the session
TOKEN_ACTIONS = {'api.api_run_query', 'api.api_test_query', 'api.api_test_routing', 'api.api_resolve_error',
                 'api.api_connection_test', 'api.api_channel_test'}
# The API token: a shorter one is too easy to guess
MIN_TOKEN_LENGTH = 32
# After LOGIN_ATTEMPTS wrong passwords, an address waits LOGIN_BLOCK_SECONDS before the next try; after
# LOGIN_FORGET_SECONDS without wrong passwords it starts again from zero. Short waits: without
# ERRORENGINE_TRUSTED_PROXY every attempt seems to come from the reverse proxy, the administrator's included
LOGIN_ATTEMPTS = 5
LOGIN_BLOCK_SECONDS = 60
LOGIN_FORGET_SECONDS = 15 * 60
_failed_logins_lock = threading.Lock()
# Requests refused by the CSRF check, which anyone can send, or to the API token: in the log the first one at
# once, then one line at most every REFUSALS_LOG_SECONDS, with how many came in between
REFUSALS_LOG_SECONDS = 60
_refusals_lock = threading.Lock()
# Set by app.js just before it reloads a live page by itself: that reload is not the user doing something
BACKGROUND_COOKIE = 'errorengine_background'


def init_auth(app):
    """Registers login/logout and the checks on every request."""
    app.register_blueprint(auth_bp)
    app.before_request(_require_login)
    app.before_request(_check_csrf)
    app.jinja_env.globals['csrf_token'] = csrf_token
    # Wrong passwords by address: (how many in a row, time of the last one). In memory, a single process
    app.extensions['failed_logins'] = {}
    app.extensions['refusals'] = {'logged': None, 'unlogged': 0}
    # Part of every session: signing out draws a new one and every copy of a cookie stops working. In
    # memory, new at every start: a restart ends the sessions too, and a copy never comes back
    app.extensions['session_generation'] = secrets.token_hex(8)

    @app.after_request
    def forget_the_background_mark(response):
        if BACKGROUND_COOKIE in request.cookies:
            response.delete_cookie(BACKGROUND_COOKIE)
        return response

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
    if token and len(token) < MIN_TOKEN_LENGTH:
        logger.warning(f"ERRORENGINE_API_TOKEN is shorter than {MIN_TOKEN_LENGTH} characters: scripts that use "
                       "it are refused. Generate a longer one with the command in docs/configuration.md (Sign-in)")


def _configuration_problem(config) -> str:
    """What is missing to use the login (empty string when everything is fine)."""
    if not config.get('LOGIN_PASSWORD_HASH'):
        return "ERRORENGINE_PASSWORD_HASH is missing from the server's .env file."
    return ''


def _credentials_fingerprint() -> str:
    """Fingerprint of username, password and generation: when one changes, open sessions are no longer valid."""
    cfg = current_app.config
    data = f"{cfg['LOGIN_USERNAME']}:{cfg['LOGIN_PASSWORD_HASH']}:{current_app.extensions['session_generation']}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def _is_logged_in() -> bool:
    """Signed in, with credentials and generation unchanged, and something done in the last hours."""
    idle = time.time() - session.get('seen', 0)
    return (bool(session.get('user')) and idle <= current_app.permanent_session_lifetime.total_seconds()
            and hmac.compare_digest(session.get('auth', ''), _credentials_fingerprint()))


def _done_by_the_user() -> bool:
    """A page opened or a change: not what a page asks by itself (the API read every 30 s, its own reload)."""
    if request.method in ('GET', 'HEAD') and request.path.startswith('/api/'):
        return False
    return BACKGROUND_COOKIE not in request.cookies


def _valid_api_token() -> bool:
    token = current_app.config.get('API_TOKEN')
    if not token or len(token) < MIN_TOKEN_LENGTH:
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
        if _done_by_the_user():
            session['seen'] = int(time.time())
        return None
    if request.path.startswith('/api/'):
        if not _configuration_problem(current_app.config) and _valid_api_token():
            if request.method in UNSAFE_METHODS and request.endpoint not in TOKEN_ACTIONS:
                _log_refusal('the API token only reads and runs the actions listed in docs/api-reference.md')
                return jsonify({'error': 'forbidden', 'message': 'The API token only reads and runs the actions '
                                                               'listed in docs/api-reference.md'}), 403
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
    site = request.headers.get('Sec-Fetch-Site', 'same-origin')
    if site not in ('same-origin', 'none'):
        reason = f'sent from another site (Sec-Fetch-Site: {site})'
    elif not session:
        reason = 'no session, the cookie is missing or expired' + (
            ': with ERRORENGINE_HTTPS=true the browser keeps it only over https://'
            if current_app.config.get('HTTPS') else '')
    elif not sent:
        reason = 'CSRF token missing'
    elif not hmac.compare_digest(sent.encode(), expected.encode()):
        reason = 'CSRF token wrong'
    else:
        return None
    _log_refusal(reason)
    if request.path.startswith('/api/'):
        return jsonify({'error': 'csrf', 'message': 'The page is out of date: reload it and try again'}), 403
    # Only into a session the browser sent: a new one would replace the cookie of whoever is signed in
    # (a page of another site posting to /login, open to everyone, gets this far without it)
    if session:
        flash('The page was out of date and has been reloaded: try again', 'warning')
    # Back to the page with the form, if it is a page of ErrorEngine; otherwise the dashboard.
    # Behind a proxy that changes the host, the browser itself can say the form was ours
    back = urlsplit(request.referrer or '')
    ours = back.netloc == request.host or request.headers.get('Sec-Fetch-Site') == 'same-origin'
    page = back.path + (f'?{back.query}' if back.query else '') if ours else ''
    return redirect(_safe_next(page))


def _log_refusal(reason: str):
    """A refused request in the log, unless one was written less than REFUSALS_LOG_SECONDS ago: then counted."""
    state, now = current_app.extensions['refusals'], time.monotonic()
    with _refusals_lock:
        if state['logged'] is not None and now - state['logged'] < REFUSALS_LOG_SECONDS:
            state['unlogged'] += 1
            return
        unlogged, state['unlogged'], state['logged'] = state['unlogged'], 0, now
    more = f" ({unlogged} more refused since the previous line)" if unlogged else ''
    # !r: a line break in the path would write a line of its own
    logger.warning(f"Refused {request.method} {request.path!r} from {request.remote_addr}: {reason}{more}")


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
                session['seen'] = int(time.time())
                logger.info(f"Login: {username} from {request.remote_addr}")
                return redirect(next_url)

            # !r: a line break in the user name would write a line of its own
            logger.warning(f"Failed login for {username!r} from {request.remote_addr}")
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
    current_app.extensions['session_generation'] = secrets.token_hex(8)
    if user:
        logger.info(f"Logout: {user}")
    return redirect(url_for('auth.login'))
