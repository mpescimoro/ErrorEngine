r"""
Single-user login, configured in .env:

    ERRORENGINE_USER            username (default: admin)
    ERRORENGINE_PASSWORD_HASH   password hash, generated with:
        venv\Scripts\python.exe -c "import getpass; from werkzeug.security import generate_password_hash as h; print(h(getpass.getpass('Password: ')))"
    ERRORENGINE_API_TOKEN       optional: to call the APIs from scripts, with the header
                                "Authorization: Bearer <token>"
    ERRORENGINE_SESSION_HOURS   hours of inactivity after which the session expires (default 12)

Every page and API requires login, except the login page, static files
and /api/health (for external checks). Until the password hash and the
SECRET_KEY are set, nobody can open the interface; the scheduler keeps running.
Changing the password or the username ends the open sessions.
"""
import hashlib
import hmac
import logging
import time
from urllib.parse import urlsplit

from flask import (Blueprint, current_app, has_request_context, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash

from config import INSECURE_SECRET_KEYS

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

# Endpoints reachable without login
PUBLIC_ENDPOINTS = {'auth.login', 'static', 'api.api_health'}


def init_auth(app):
    """Registers login/logout and the check on every request."""
    app.register_blueprint(auth_bp)
    app.before_request(_require_login)

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


def _configuration_problem(config) -> str:
    """What is missing to use the login (empty string when everything is fine)."""
    if not config.get('LOGIN_PASSWORD_HASH'):
        return "ERRORENGINE_PASSWORD_HASH is missing from the server's .env file."
    if not config.get('SECRET_KEY') or config.get('SECRET_KEY') in INSECURE_SECRET_KEYS:
        return "The SECRET_KEY in the server's .env file is missing or is the example one."
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
    if not token:
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

    error = None
    if request.method == 'POST' and not problem:
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
            session.clear()
            session.permanent = True
            session['user'] = username
            session['auth'] = _credentials_fingerprint()
            logger.info(f"Login: {username} from {request.remote_addr}")
            return redirect(next_url)

        logger.warning(f"Failed login for '{username}' from {request.remote_addr}")
        time.sleep(1)  # slows down bursts of attempts
        error = 'Wrong username or password.'

    return render_template('login.html', error=error, problem=problem, next_url=next_url,
                           username=request.form.get('username', ''))


@auth_bp.route('/logout', methods=['POST'])
def logout():
    user = session.get('user')
    session.clear()
    if user:
        logger.info(f"Logout: {user}")
    return redirect(url_for('auth.login'))
