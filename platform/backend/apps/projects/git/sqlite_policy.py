"""AUTOKOM: advisory detection of ephemeral SQLite files in client backends.

Checks are informational: SQLite alone does not reject a deployment.
"""
import re
import os
from pathlib import Path

POLICY_MESSAGE = ('SQLite détectée : cette application conserve potentiellement ses données '
                  'dans un fichier du conteneur. Lors du redéploiement ou du remplacement '
                  'du conteneur, les données non persistées peuvent être perdues. '
                  'Sauvegardez vos données ou utilisez PostgreSQL/MySQL avec un stockage durable.')

_SKIP_DIRS = frozenset({'.git', 'node_modules', 'vendor', '.venv', 'venv', '__pycache__',
    '.next', 'dist', 'build', 'target', 'coverage', '.gradle', 'tests', 'test',
    '.pytest_cache', '.mypy_cache', 'site-packages'})
_TEXT_SUFFIXES = frozenset({'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.php', '.json',
    '.toml', '.prisma', '.xml', '.gradle', '.kts', '.properties', '.yaml', '.yml', '.env'})
_STATIC_FILES = frozenset({'requirements.txt', 'pyproject.toml', 'composer.json',
    'package.json', 'pom.xml', 'build.gradle', 'build.gradle.kts', '.env',
    '.env.production', '.env.local'})
_RULES = (
    ('Import Python sqlite3', re.compile(r'(?m)^\s*(?:import\s+(?:sqlite3|pysqlite3)\b|from\s+(?:sqlite3|pysqlite3)\s+import\b)')),
    ('Connexion SQLite locale', re.compile(r'(?i)\b(?:sqlite3\.connect|sqlite\.connect|jdbc:sqlite:|sqlite(?:\+\w+)?://|sqlite:/{1,2})')),
    ('Moteur Django SQLite', re.compile(r'(?i)django\.db\.backends\.sqlite3')),
    ('Dépendance SQLite', re.compile(r'(?i)["\'](?:better-sqlite3|sqlite3|sqlite|sql\.js|@libsql/client|@prisma/adapter-better-sqlite3|@mikro-orm/sqlite|sqlite-jdbc)["\']\s*[:=]|org\.xerial:sqlite-jdbc|<groupId>org\.xerial</groupId>\s*<artifactId>sqlite-jdbc')),
    ('Prisma SQLite', re.compile(r'(?i)\bprovider\s*=\s*["\']sqlite["\']')),
    ('PHP SQLite', re.compile(r'(?i)\bnew\s+\\?SQLite3\s*\(|\bPDO\s*\(\s*["\']sqlite:|\bPDO_SQLITE\b')),
    ('Configuration SQLite explicite', re.compile(r'(?im)^\s*(?:DB_CONNECTION|DATABASE_ENGINE|DATABASE_URL)\s*=\s*["\']?(?:sqlite(?:3)?|file:|sqlite:|sqlite\+)[^\s#]*')),
    ('Configuration SQLite YAML', re.compile(r'(?im)^\s*(?:driver|dialect|database-type)\s*:\s*["\']?sqlite\b')),
)


def detect_text(name, content):
    """Return a generic reason, never the source text or credentials."""
    if not isinstance(content, str):
        return ''
    # Avoid blocking documentation and files that are not runtime configuration.
    if name.endswith(('.md', '.rst', '.txt')) and name not in _STATIC_FILES:
        return ''
    for label, pattern in _RULES:
        if pattern.search(content):
            return label
    return ''


def detect_sqlite_source(path, *, max_files=3000, max_bytes=256 * 1024):
    """Check backend source, pruning generated trees before visiting files."""
    root = Path(path).resolve(strict=True)
    checked = 0
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        # Pruning (instead of Path.rglob) avoids traversing huge node_modules/vendor.
        dirs[:] = sorted(name for name in dirs
                         if name not in _SKIP_DIRS and not (base / name).is_symlink())
        for name in sorted(files):
            entry = base / name
            if entry.is_symlink() or not entry.is_file():
                continue
            relative = entry.relative_to(root)
            checked += 1
            if checked > max_files:
                raise ValueError('Contrôle SQLite impossible : trop de fichiers source à analyser.')
            if entry.name.lower().endswith(('.sqlite', '.sqlite3', '.db3')):
                return str(relative), 'Fichier de base SQLite'
            if entry.suffix.lower() == '.db':
                with entry.open('rb') as database_file:
                    if database_file.read(16) == b'SQLite format 3\x00':
                        return str(relative), 'Fichier de base SQLite'
            if not (entry.suffix.lower() in _TEXT_SUFFIXES or entry.name in _STATIC_FILES
                    or entry.name.startswith('.env')):
                continue
            size = entry.stat().st_size
            if size > max_bytes:
                if entry.name in ('app.py', 'main.py', 'settings.py', 'package.json',
                                  'composer.json', 'pyproject.toml'):
                    raise ValueError('Contrôle SQLite impossible : fichier de configuration trop volumineux.')
                continue
            content = entry.read_text(encoding='utf-8', errors='replace')
            reason = detect_text(entry.as_posix(), content)
            if reason:
                return str(relative), reason
    return None


def sqlite_storage_warning(path):
    """Return a non-blocking warning for SQLite use in checked-out sources."""
    issue = detect_sqlite_source(path)
    if issue:
        filename, reason = issue
        return f'{POLICY_MESSAGE} Détection : {reason} ({filename}).'
    return ''
