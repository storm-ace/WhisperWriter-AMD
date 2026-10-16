"""
MCP server that lets Claude Code read your WhisperWriter dictation sessions.

Speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0) and only
needs the Python standard library, so it starts instantly. The session database is
opened read-only; the running app can keep writing to it at the same time (WAL mode).

Register it with Claude Code:

    claude mcp add --scope user whisperwriter -- python /path/to/src/mcp_server.py

Optional: `--db <path>` to point at a non-default session database.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sessions import SessionStore, default_db_path  # noqa: E402

SERVER_NAME = 'whisperwriter'
SERVER_VERSION = '2.0.0'
DEFAULT_PROTOCOL = '2025-06-18'

TOOLS = [
    {
        'name': 'search_dictations',
        'description': ('Full-text search across all of the user\'s past voice dictations. '
                        'Use this to recover context the user dictated earlier (requirements, '
                        'decisions, names) before asking them to repeat it.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'Words to search for.'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 15},
            },
            'required': ['query'],
        },
        'annotations': {'readOnlyHint': True},
    },
    {
        'name': 'list_sessions',
        'description': 'List recent dictation sessions (newest first) with id, title, time and size.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 20},
            },
        },
        'annotations': {'readOnlyHint': True},
    },
    {
        'name': 'get_session',
        'description': ('Full transcript of one dictation session as Markdown. Omit session_id '
                        'for the most recent session.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'session_id': {'type': 'integer', 'description': 'Session id from list_sessions.'},
            },
        },
        'annotations': {'readOnlyHint': True},
    },
    {
        'name': 'recent_dictations',
        'description': 'The latest dictations across all sessions, oldest first.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 20},
                'since_minutes': {'type': 'integer', 'minimum': 1,
                                  'description': 'Only dictations from the last N minutes.'},
            },
        },
        'annotations': {'readOnlyHint': True},
    },
]


def _fmt_time(ts):
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))


class Server:
    def __init__(self, db_path):
        self.db_path = db_path
        self._store = None

    @property
    def store(self):
        # Opened lazily so the server still answers `initialize` before the app ever ran.
        if self._store is None:
            self._store = SessionStore(self.db_path, read_only=True)
        return self._store

    # ------------------------------------------------------------------ tools
    def tool_search_dictations(self, query, limit=15):
        hits = self.store.search(query, limit=int(limit))
        if not hits:
            return f'No dictations match "{query}".'
        lines = [f'{len(hits)} match(es) for "{query}":', '']
        for h in hits:
            lines.append(f"- [{_fmt_time(h['created_at'])}] session {h['session_id']} "
                         f"\"{h['session_title'] or 'Untitled'}\": {h['snippet']}")
        lines.append('')
        lines.append('Use get_session with a session id for the full transcript.')
        return '\n'.join(lines)

    def tool_list_sessions(self, limit=20):
        sessions = self.store.list_sessions(limit=int(limit))
        if not sessions:
            return 'No sessions yet.'
        lines = []
        for s in sessions:
            app = f" · {s['last_app']}" if s.get('last_app') else ''
            lines.append(f"- id {s['id']}: \"{s['title'] or 'Untitled'}\" — "
                         f"{_fmt_time(s['created_at'])} · {s['dictation_count']} dictations · "
                         f"{s['word_count']} words{app}")
        return '\n'.join(lines)

    def tool_get_session(self, session_id=None):
        sid = session_id or self.store.latest_session_id()
        if not sid:
            return 'No sessions yet.'
        md = self.store.session_markdown(int(sid))
        return md or f'Session {sid} not found.'

    def tool_recent_dictations(self, limit=20, since_minutes=None):
        items = self.store.recent_dictations(limit=int(limit), since_minutes=since_minutes)
        if not items:
            return 'No recent dictations.'
        return '\n'.join(
            f"- [{_fmt_time(d['created_at'])}] (session {d['session_id']}) {d['text']}"
            for d in items)

    # ------------------------------------------------------------------ protocol
    def handle(self, msg):
        method = msg.get('method')
        params = msg.get('params') or {}
        if method == 'initialize':
            return {
                'protocolVersion': params.get('protocolVersion') or DEFAULT_PROTOCOL,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
                'instructions': ('Read-only access to the user\'s voice dictation history from '
                                 'WhisperWriter. Search it when the user refers to something '
                                 'they said or dictated earlier.'),
            }
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': TOOLS}
        if method == 'tools/call':
            name = params.get('name')
            args = params.get('arguments') or {}
            fn = getattr(self, f'tool_{name}', None)
            if fn is None or name not in {t['name'] for t in TOOLS}:
                raise RpcError(-32602, f'Unknown tool: {name}')
            try:
                text = fn(**args)
                return {'content': [{'type': 'text', 'text': text}]}
            except FileNotFoundError:
                return {'content': [{'type': 'text', 'text':
                                     'No dictations recorded yet (session database not found at '
                                     f'{self.db_path}).'}], 'isError': True}
            except Exception as e:  # report tool failures to the model, not as protocol errors
                return {'content': [{'type': 'text', 'text': f'Error: {e}'}], 'isError': True}
        raise RpcError(-32601, f'Method not found: {method}')


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def serve(db_path):
    server = Server(db_path)
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {'jsonrpc': '2.0', 'id': None,
                     'error': {'code': -32700, 'message': 'Parse error'}}
            out.write(json.dumps(reply) + '\n')
            out.flush()
            continue
        if 'id' not in msg:
            continue  # notification (e.g. notifications/initialized)
        try:
            reply = {'jsonrpc': '2.0', 'id': msg['id'], 'result': server.handle(msg)}
        except RpcError as e:
            reply = {'jsonrpc': '2.0', 'id': msg['id'],
                     'error': {'code': e.code, 'message': e.message}}
        except Exception as e:
            reply = {'jsonrpc': '2.0', 'id': msg['id'],
                     'error': {'code': -32603, 'message': str(e)}}
        out.write(json.dumps(reply, ensure_ascii=False) + '\n')
        out.flush()


def main():
    parser = argparse.ArgumentParser(description='WhisperWriter MCP server (stdio)')
    parser.add_argument('--db', default=os.environ.get('WHISPERWRITER_DB') or default_db_path(),
                        help='Path to sessions.db')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', newline='\n')
        sys.stdin.reconfigure(encoding='utf-8')
    serve(args.db)


if __name__ == '__main__':
    main()
