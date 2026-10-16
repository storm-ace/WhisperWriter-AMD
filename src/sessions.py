"""
Local session store for dictations (SQLite + FTS5).

Every transcription is saved as a *dictation* inside a *session*. A new session starts
automatically after a configurable idle gap, or manually from the UI. Sessions are kept
in one SQLite file so they can be searched later — by the app itself and by Claude Code
through the MCP server (`mcp_server.py`), which opens the same file read-only.

This module has no Qt or config dependency on purpose: the MCP server imports it as a
standalone process.
"""

import os
import re
import sqlite3
import sys
import time

SCHEMA_VERSION = 1


def default_db_path():
    """Per-user data location: %APPDATA%\\WhisperWriter on Windows, XDG data dir elsewhere."""
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        folder = os.path.join(base, 'WhisperWriter')
    elif sys.platform == 'darwin':
        folder = os.path.expanduser('~/Library/Application Support/WhisperWriter')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
        folder = os.path.join(base, 'whisperwriter')
    return os.path.join(folder, 'sessions.db')


def fts_query(text):
    """Turn free text into a safe FTS5 query: every word becomes a quoted prefix term."""
    words = re.findall(r'\w+', text or '', flags=re.UNICODE)
    return ' '.join('"{}"*'.format(w.replace('"', '""')) for w in words)


def title_from_text(text, max_words=7):
    words = (text or '').strip().split()
    title = ' '.join(words[:max_words]).rstrip('.,;:!?')
    if len(words) > max_words:
        title += '…'
    return title or 'Untitled session'


class SessionStore:
    def __init__(self, path=None, read_only=False):
        self.path = path or default_db_path()
        self.read_only = read_only
        if read_only:
            if not os.path.isfile(self.path):
                raise FileNotFoundError(f'No session database at {self.path}')
            uri = 'file:{}?mode=ro'.format(self.path.replace('\\', '/'))
            self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        else:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.conn = sqlite3.connect(self.path, check_same_thread=False)
            self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.has_fts = False
        if not read_only:
            self._migrate()
        self.has_fts = self._table_exists('dictations_fts')

    # ------------------------------------------------------------------ schema
    def _table_exists(self, name):
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
        return row is not None

    def _migrate(self):
        c = self.conn
        c.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY,
                title       TEXT,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                pinned      INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS dictations (
                id          INTEGER PRIMARY KEY,
                session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                created_at  REAL NOT NULL,
                text        TEXT NOT NULL,
                raw_text    TEXT,
                duration_s  REAL,
                latency_s   REAL,
                target_app  TEXT,
                target_title TEXT,
                engine      TEXT,
                corrected   INTEGER NOT NULL DEFAULT 0,
                word_count  INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS ix_dictations_session ON dictations(session_id, created_at);
            CREATE INDEX IF NOT EXISTS ix_sessions_updated ON sessions(updated_at);
        ''')
        try:
            c.executescript('''
                CREATE VIRTUAL TABLE IF NOT EXISTS dictations_fts USING fts5(
                    text, content='dictations', content_rowid='id', tokenize='unicode61');
                CREATE TRIGGER IF NOT EXISTS dictations_ai AFTER INSERT ON dictations BEGIN
                    INSERT INTO dictations_fts(rowid, text) VALUES (new.id, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS dictations_ad AFTER DELETE ON dictations BEGIN
                    INSERT INTO dictations_fts(dictations_fts, rowid, text) VALUES ('delete', old.id, old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS dictations_au AFTER UPDATE OF text ON dictations BEGIN
                    INSERT INTO dictations_fts(dictations_fts, rowid, text) VALUES ('delete', old.id, old.text);
                    INSERT INTO dictations_fts(rowid, text) VALUES (new.id, new.text);
                END;
            ''')
        except sqlite3.OperationalError:
            pass  # SQLite without FTS5: search falls back to LIKE
        c.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
        c.commit()

    # ------------------------------------------------------------------ writes
    def create_session(self, title=None):
        now = time.time()
        cur = self.conn.execute(
            'INSERT INTO sessions (title, created_at, updated_at) VALUES (?, ?, ?)',
            (title, now, now))
        self.conn.commit()
        return cur.lastrowid

    def latest_session_id(self):
        row = self.conn.execute(
            'SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1').fetchone()
        return row['id'] if row else None

    def resolve_active_session(self, gap_minutes):
        """Latest session if it was used within `gap_minutes`, else a fresh one."""
        row = self.conn.execute(
            'SELECT id, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 1').fetchone()
        if row and (not gap_minutes or time.time() - row['updated_at'] < gap_minutes * 60):
            return row['id']
        return self.create_session()

    def touch_session(self, session_id):
        self.conn.execute('UPDATE sessions SET updated_at = ? WHERE id = ?',
                          (time.time(), session_id))
        self.conn.commit()

    def add_dictation(self, session_id, text, raw_text=None, duration_s=None, latency_s=None,
                      target_app=None, target_title=None, engine=None, corrected=False):
        now = time.time()
        text = (text or '').strip()
        cur = self.conn.execute(
            '''INSERT INTO dictations (session_id, created_at, text, raw_text, duration_s,
                   latency_s, target_app, target_title, engine, corrected, word_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (session_id, now, text, raw_text, duration_s, latency_s, target_app,
             target_title, engine, int(bool(corrected)), len(text.split())))
        self.conn.execute(
            'UPDATE sessions SET updated_at = ?, title = COALESCE(title, ?) WHERE id = ?',
            (now, title_from_text(text), session_id))
        self.conn.commit()
        return cur.lastrowid

    def rename_session(self, session_id, title):
        self.conn.execute('UPDATE sessions SET title = ? WHERE id = ?',
                          (title.strip() or None, session_id))
        self.conn.commit()

    def delete_session(self, session_id):
        self.conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        self.conn.commit()

    def delete_dictation(self, dictation_id):
        self.conn.execute('DELETE FROM dictations WHERE id = ?', (dictation_id,))
        self.conn.commit()

    def prune_empty_sessions(self, keep_id=None):
        """Drop sessions that never got a dictation (except `keep_id`)."""
        self.conn.execute(
            '''DELETE FROM sessions WHERE id != COALESCE(?, -1)
               AND NOT EXISTS (SELECT 1 FROM dictations d WHERE d.session_id = sessions.id)''',
            (keep_id,))
        self.conn.commit()

    # ------------------------------------------------------------------ reads
    _SESSION_COLS = '''s.id, s.title, s.created_at, s.updated_at, s.pinned,
        COUNT(d.id) AS dictation_count,
        COALESCE(SUM(d.word_count), 0) AS word_count,
        COALESCE(SUM(d.duration_s), 0) AS audio_s,
        (SELECT target_app FROM dictations x WHERE x.session_id = s.id
            ORDER BY x.created_at DESC LIMIT 1) AS last_app'''

    def list_sessions(self, limit=200, offset=0):
        rows = self.conn.execute(
            f'''SELECT {self._SESSION_COLS} FROM sessions s
                LEFT JOIN dictations d ON d.session_id = s.id
                GROUP BY s.id ORDER BY s.pinned DESC, s.updated_at DESC LIMIT ? OFFSET ?''',
            (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id):
        row = self.conn.execute(
            f'''SELECT {self._SESSION_COLS} FROM sessions s
                LEFT JOIN dictations d ON d.session_id = s.id
                WHERE s.id = ? GROUP BY s.id''', (session_id,)).fetchone()
        return dict(row) if row else None

    def dictations(self, session_id, limit=None):
        sql = 'SELECT * FROM dictations WHERE session_id = ? ORDER BY created_at'
        params = [session_id]
        if limit:
            sql = f'SELECT * FROM ({sql} DESC LIMIT ?) ORDER BY created_at'
            params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def recent_dictations(self, limit=20, since_minutes=None):
        sql = '''SELECT d.*, s.title AS session_title FROM dictations d
                 JOIN sessions s ON s.id = d.session_id'''
        params = []
        if since_minutes:
            sql += ' WHERE d.created_at >= ?'
            params.append(time.time() - since_minutes * 60)
        sql += ' ORDER BY d.created_at DESC LIMIT ?'
        params.append(limit)
        rows = [dict(r) for r in self.conn.execute(sql, params).fetchall()]
        return list(reversed(rows))

    def search(self, query, limit=20):
        """Full-text search over dictations; returns hits with a highlighted snippet."""
        q = fts_query(query)
        if not q:
            return []
        if self.has_fts:
            try:
                rows = self.conn.execute(
                    '''SELECT d.id, d.session_id, d.created_at, d.text, d.target_app,
                              s.title AS session_title,
                              snippet(dictations_fts, 0, '[', ']', '…', 14) AS snippet
                       FROM dictations_fts
                       JOIN dictations d ON d.id = dictations_fts.rowid
                       JOIN sessions s ON s.id = d.session_id
                       WHERE dictations_fts MATCH ?
                       ORDER BY bm25(dictations_fts), d.created_at DESC LIMIT ?''',
                    (q, limit)).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass
        like = '%' + query.strip() + '%'
        rows = self.conn.execute(
            '''SELECT d.id, d.session_id, d.created_at, d.text, d.target_app,
                      s.title AS session_title, d.text AS snippet
               FROM dictations d JOIN sessions s ON s.id = d.session_id
               WHERE d.text LIKE ? ORDER BY d.created_at DESC LIMIT ?''',
            (like, limit)).fetchall()
        return [dict(r) for r in rows]

    def search_sessions(self, query, limit=50):
        """Distinct sessions with at least one matching dictation, best match first."""
        seen, out = set(), []
        for hit in self.search(query, limit=limit * 4):
            if hit['session_id'] in seen:
                continue
            seen.add(hit['session_id'])
            out.append(hit)
            if len(out) >= limit:
                break
        return out

    def stats(self, last_n=20):
        row = self.conn.execute(
            '''SELECT AVG(latency_s) AS avg_latency, AVG(duration_s) AS avg_duration
               FROM (SELECT latency_s, duration_s FROM dictations
                     WHERE latency_s IS NOT NULL ORDER BY created_at DESC LIMIT ?)''',
            (last_n,)).fetchone()
        total = self.conn.execute('SELECT COUNT(*) AS n FROM sessions').fetchone()['n']
        return {'avg_latency': row['avg_latency'], 'avg_duration': row['avg_duration'],
                'session_count': total}

    # ------------------------------------------------------------------ export
    def session_markdown(self, session_id, for_claude=False):
        """Session as Markdown; `for_claude` wraps it as a context block for a prompt."""
        session = self.get_session(session_id)
        if not session:
            return ''
        items = self.dictations(session_id)
        title = session['title'] or 'Untitled session'
        started = time.strftime('%Y-%m-%d %H:%M', time.localtime(session['created_at']))
        lines = []
        for d in items:
            stamp = time.strftime('%H:%M', time.localtime(d['created_at']))
            target = f" → {d['target_app']}" if d.get('target_app') else ''
            lines.append(f"- **{stamp}**{target}: {d['text']}")
        body = '\n'.join(lines) or '_(no dictations)_'
        if for_claude:
            return (f'<dictation-session title="{title}" started="{started}">\n'
                    f'Context from my earlier voice dictation (WhisperWriter), oldest first:\n\n'
                    f'{body}\n</dictation-session>\n')
        return f'## {title}\n\n_Started {started} · {len(items)} dictations_\n\n{body}\n'

    def close(self):
        self.conn.close()
