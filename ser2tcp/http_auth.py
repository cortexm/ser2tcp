"""Authentication and session management"""

import hashlib as _hashlib
import re as _re
import secrets as _secrets
import time as _time


DEFAULT_SESSION_TIMEOUT = 3600

# What delete_user()/delete_token() answer when the only thing standing
# in the way is that nobody has said yes yet: the account is the last
# one there is, so removing it turns authentication off altogether.
#
# A plain string, so a caller that knows nothing about it still reads
# the answer as a refusal rather than as success. A caller that can
# offer the confirmation has to test for it *before* the generic "a
# string means an error" branch, or the one refusal it could act on
# reads like the one it cannot.
CONFIRM_DISABLE_AUTH = 'confirm:disable_auth'

# Removing the last admin while other accounts remain is the other
# outcome, and it is not offered a confirmation: authentication would
# stay on with nobody able to administer it. That is not the userless
# mode, it is being locked out - the same state add_token() refuses to
# create from the other end.
LAST_ADMIN_ERROR = (
    'Cannot delete the last admin while other accounts remain: delete '
    'them as well, or make one of them an admin first')

# sha256:<salt hex>:<digest hex>, exactly as hash_password() writes it.
# The digest length is fixed by the algorithm; the salt is left open
# because an older config may carry one of a different length. Lower
# case only - an upper-case digest never verified anyway, since the
# comparison is against a freshly computed lower-case one.
_HASH_RE = _re.compile(r'^sha256:[0-9a-f]+:[0-9a-f]{64}$')


def is_password_hash(value):
    """True if `value` is a stored password hash rather than a password.

    The prefix alone is not an answer: a password may start with
    "sha256:" like any other string. Deciding on the whole shape leaves
    only passwords that are a well-formed digest, which is a far smaller
    thing to be unlucky about.
    """
    return isinstance(value, str) and bool(_HASH_RE.match(value))


def hash_password(password):
    """Hash password with SHA-256 and random salt"""
    if not isinstance(password, str):
        raise TypeError('password must be a string')
    salt = _secrets.token_hex(16)
    h = _hashlib.sha256((salt + password).encode()).hexdigest()
    return f"sha256:{salt}:{h}"


def ensure_hashed(password):
    """Return password as hash - hash if plain, keep if already hashed.

    Trusting the prefix stored a password that merely began with
    "sha256:" as though it were already hashed. Nothing complained, and
    the account was created with a password nobody could ever sign in
    with: verify_password() hashes what it is given and compares.
    """
    if not isinstance(password, str):
        raise TypeError('password must be a string')
    if is_password_hash(password):
        return password
    return hash_password(password)


def verify_password(password, stored):
    """Verify password against stored hash.

    Both arguments can come straight out of a JSON body or a config
    file, so neither is guaranteed to be a string. A wrong type is an
    answer to this question - "no" - not a reason to raise.
    """
    if not isinstance(password, str) or not is_password_hash(stored):
        return False
    _, salt, expected = stored.split(':')
    h = _hashlib.sha256((salt + password).encode()).hexdigest()
    return _secrets.compare_digest(h, expected)


# Something to hash against when the login does not exist, so that the
# answer takes the same work either way. Built from a random secret at
# import: no password matches it, and none can be constructed to.
DUMMY_HASH = hash_password(_secrets.token_hex(32))


def _check_timeout(value, where):
    """Raise ValueError unless `value` is a usable session timeout"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or value < 0:
        raise ValueError(
            f"{where}: session_timeout must be a non-negative number")


class SessionManager():
    """Manage authentication sessions"""

    def __init__(self, config):
        """Build the manager from a users/tokens config block.

        Raises ValueError on an entry that cannot be read. Skipping
        those instead would be far worse than refusing to start: drop
        every user and is_empty turns authentication off altogether,
        so a typo in one login would silently publish the whole API.
        """
        self._users = {}
        self._tokens = {}
        self._sessions = {}
        self._configured_timeout = None
        self.set_default_timeout(config.get('session_timeout'))
        for index, user in enumerate(config.get('users', [])):
            self._users[self._check_user(user, index)] = user
        for index, token_cfg in enumerate(config.get('tokens', [])):
            self._tokens[self._check_token(token_cfg, index)] = token_cfg

    @staticmethod
    def _check_user(user, index):
        """Validate one configured user, return its login"""
        where = f'users[{index}]'
        if not isinstance(user, dict):
            raise ValueError(f'{where}: must be an object')
        login = user.get('login')
        if not isinstance(login, str) or not login:
            raise ValueError(f'{where}: login must be a non-empty string')
        if not isinstance(user.get('password'), str):
            raise ValueError(f'{where} ({login}): password must be a string')
        if 'session_timeout' in user:
            _check_timeout(user['session_timeout'], f'{where} ({login})')
        return login

    @staticmethod
    def _check_token(token_cfg, index):
        """Validate one configured API token, return the token itself"""
        where = f'tokens[{index}]'
        if not isinstance(token_cfg, dict):
            raise ValueError(f'{where}: must be an object')
        token = token_cfg.get('token')
        if not isinstance(token, str) or not token:
            raise ValueError(f'{where}: token must be a non-empty string')
        return token

    @property
    def is_empty(self):
        """True if no users and no tokens configured"""
        return not self._users and not self._tokens

    def login(self, login, password):
        """Authenticate user, return session token or None.

        Both arguments come from a request body on an endpoint that
        needs no authentication, so neither is guaranteed to be a
        string - and an unhashable one would raise out of the lookup
        below rather than simply failing to match.

        A login that does not exist is hashed against a stand-in rather
        than answered straight away. Returning early made "no such
        user" measurably quicker than "wrong password", which is enough
        to read a list of accounts off the clock.
        """
        if not isinstance(login, str):
            return None
        user = self._users.get(login)
        matched = verify_password(
            password, user['password'] if user else DUMMY_HASH)
        if not user or not matched:
            return None
        return self.create_session(login)

    def create_session(self, login):
        """Create session for user without password check, return token.

        The session holds who it belongs to and when it lapses, and
        nothing else. What the login is allowed to do is read off the
        user every time it is used - a copy taken here would go on
        granting what the account no longer has.
        """
        user = self._users.get(login)
        if not user:
            return None
        token = _secrets.token_hex(32)
        self._sessions[token] = {
            'login': login,
            'expires': _time.time() + self._timeout_for(user),
        }
        return token

    @property
    def configured_timeout(self):
        """The session timeout the config sets, or None for the default"""
        return self._configured_timeout

    def set_default_timeout(self, value):
        """Set the session timeout, None meaning the default.

        Kept apart from the default so that "not set" survives being
        written back: saving 3600 into the file would be a choice nobody
        made. Sessions already running keep the expiry they were given
        and take the new one when they are next used.
        Raises ValueError on a value that is not a usable timeout.
        """
        if value is not None:
            _check_timeout(value, 'config')
        self._configured_timeout = value

    @property
    def _default_timeout(self):
        if self._configured_timeout is None:
            return DEFAULT_SESSION_TIMEOUT
        return self._configured_timeout

    def _timeout_for(self, user):
        """How long this user's sessions last between requests"""
        timeout = user.get('session_timeout')
        return self._default_timeout if timeout is None else timeout

    def _drop_sessions(self, login):
        """End every session belonging to this login"""
        for token in [t for t, s in self._sessions.items()
                      if s['login'] == login]:
            del self._sessions[token]

    def logout(self, token):
        """Remove session"""
        self._sessions.pop(token, None)

    def authenticate(self, token):
        """Validate token (session or API), return user info or None"""
        # Check API tokens first
        token_cfg = self._tokens.get(token)
        if token_cfg:
            return {
                'login': token_cfg.get('name', 'token'),
                'admin': token_cfg.get('admin', False),
            }
        # Check sessions
        session = self._sessions.get(token)
        if not session:
            return None
        if _time.time() > session['expires']:
            del self._sessions[token]
            return None
        user = self._users.get(session['login'])
        if user is None:
            # The account went away without going through delete_user.
            del self._sessions[token]
            return None
        # Renew session
        session['expires'] = _time.time() + self._timeout_for(user)
        return {
            'login': session['login'],
            'admin': user.get('admin', False),
        }

    def session_state(self, token):
        """What this token grants right now, without renewing it.

        authenticate() is the answer for a request: it pushes the
        expiry out, because a request is activity. A connection held
        open for hours is not, and has to be able to ask the same
        question without keeping the session alive by asking.
        """
        token_cfg = self._tokens.get(token)
        if token_cfg:
            return {
                'login': token_cfg.get('name', 'token'),
                'admin': token_cfg.get('admin', False),
            }
        session = self._sessions.get(token)
        if not session or _time.time() > session['expires']:
            return None
        user = self._users.get(session['login'])
        if user is None:
            return None
        return {'login': session['login'], 'admin': user.get('admin', False)}

    def list_users(self):
        """Return list of users (without passwords)"""
        return [
            {k: v for k, v in user.items() if k != 'password'}
            for user in self._users.values()]

    def add_user(self, login, password, **kwargs):
        """Add user, return True on success, False if exists.
        First user is always admin."""
        if login in self._users:
            return False
        user = {'login': login, 'password': ensure_hashed(password)}
        if not self._users:
            user['admin'] = True
        else:
            if 'admin' in kwargs:
                user['admin'] = kwargs['admin']
        self._set_timeout(user, kwargs)
        self._users[login] = user
        return True

    @staticmethod
    def _set_timeout(user, kwargs):
        """Apply a session_timeout override, where None means "default".

        Storing None would land in create_session() as
        time.time() + None, so the key is removed instead - the same
        meaning null has for the global setting.
        """
        if 'session_timeout' not in kwargs:
            return
        if kwargs['session_timeout'] is None:
            user.pop('session_timeout', None)
        else:
            user['session_timeout'] = kwargs['session_timeout']

    def update_user(self, login, **kwargs):
        """Update user, return True on success, False/string on error"""
        user = self._users.get(login)
        if not user:
            return False
        if 'admin' in kwargs and not kwargs['admin']:
            if user.get('admin') and self._admin_count() <= 1:
                if self._admin_token_count() == 0:
                    return 'Cannot remove last admin'
        if 'password' in kwargs:
            user['password'] = ensure_hashed(kwargs['password'])
            # Changing a password is how an account that got out is
            # shut again. Leaving its sessions open would make that
            # pointless: whoever is already in stays in, and every
            # request they make pushes the expiry further out.
            self._drop_sessions(login)
        if 'admin' in kwargs:
            user['admin'] = kwargs['admin']
        self._set_timeout(user, kwargs)
        return True

    def _admin_count(self):
        """Count admin users"""
        return sum(1 for u in self._users.values() if u.get('admin'))

    def _admin_token_count(self):
        """Count admin tokens"""
        return sum(1 for t in self._tokens.values() if t.get('admin'))

    def delete_user(self, login, disable_auth=False):
        """Delete user, return True on success, False/string on error.

        Two different things can be meant by "the last admin", and they
        need opposite answers.

        Deleting the last account *of any kind* turns authentication
        off: is_empty goes True and _require_auth() then hands admin to
        everybody. That is a real thing to want - it is how an
        installation goes back to being open - so it is allowed, but
        only once the caller says so through `disable_auth`. Until
        then the answer is CONFIRM_DISABLE_AUTH.

        Deleting the last admin while *other* accounts remain leaves
        authentication on with nobody able to administer it. No
        confirmation is offered for that one, because there is nothing
        to weigh: it is not a choice, it is a mistake.
        """
        if login not in self._users:
            return False
        user = self._users[login]
        if len(self._users) == 1 and not self._tokens:
            if not disable_auth:
                return CONFIRM_DISABLE_AUTH
        elif user.get('admin') and self._admin_count() <= 1:
            if self._admin_token_count() == 0:
                return LAST_ADMIN_ERROR
        del self._users[login]
        self._drop_sessions(login)
        return True

    def list_tokens(self):
        """Return list of API tokens"""
        return list(self._tokens.values())

    def add_token(self, token, name, admin=False):
        """Add API token, return True on success, False if exists"""
        if token in self._tokens:
            return False
        self._tokens[token] = {'token': token, 'name': name, 'admin': admin}
        return True

    def update_token(self, token, **kwargs):
        """Update API token, return True on success, False/string on error"""
        token_cfg = self._tokens.get(token)
        if not token_cfg:
            return False
        if 'admin' in kwargs and not kwargs['admin']:
            if token_cfg.get('admin') and self._admin_token_count() <= 1:
                if self._admin_count() == 0:
                    return 'Cannot remove last admin'
        new_token = kwargs.get('token')
        if new_token and new_token != token:
            if new_token in self._tokens:
                return 'Token already exists'
            del self._tokens[token]
            token_cfg['token'] = new_token
            self._tokens[new_token] = token_cfg
        if 'name' in kwargs:
            token_cfg['name'] = kwargs['name']
        if 'admin' in kwargs:
            token_cfg['admin'] = kwargs['admin']
        return True

    def delete_token(self, token, disable_auth=False):
        """Delete API token, return True on success, False/string on error.

        The mirror of delete_user(), and deliberately so: leaving the
        way out open through users but not through tokens would only
        mean the installation that has no users cannot take it.
        """
        if token not in self._tokens:
            return False
        token_cfg = self._tokens[token]
        if len(self._tokens) == 1 and not self._users:
            if not disable_auth:
                return CONFIRM_DISABLE_AUTH
        elif token_cfg.get('admin') and self._admin_token_count() <= 1:
            if self._admin_count() == 0:
                return LAST_ADMIN_ERROR
        del self._tokens[token]
        return True

    def get_auth_config(self):
        """Return auth config for persistence"""
        config = {}
        if self._configured_timeout is not None:
            config['session_timeout'] = self._configured_timeout
        if self._users:
            config['users'] = list(self._users.values())
        if self._tokens:
            config['tokens'] = list(self._tokens.values())
        return config

    def cleanup(self):
        """Remove expired sessions"""
        now = _time.time()
        expired = [
            t for t, s in self._sessions.items()
            if now > s['expires']]
        for token in expired:
            del self._sessions[token]
