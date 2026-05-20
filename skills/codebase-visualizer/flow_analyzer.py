"""
App-flow analyzer for Next.js App Router projects.
Orchestrates page_parser.py for component-level analysis.
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# ── Optional page_parser import ───────────────────────────────────
_SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(_SKILL_DIR))
try:
    import page_parser as _pp
    HAS_PARSER = True
except Exception:
    HAS_PARSER = False

# ── Packages to ignore as services ────────────────────────────────
IGNORE_PKG = {
    'fs', 'os', 'path', 'url', 'http', 'https', 'crypto', 'buffer', 'stream',
    'events', 'util', 'child_process', 'readline', 'net', 'tls', 'dns', 'assert',
    'module', 'process', 'console', 'timers', 'perf_hooks', 'v8', 'vm', 'worker_threads',
    'react', 'react-dom', 'next', 'clsx', 'classnames', 'class-variance-authority',
    'tailwind-merge', 'tailwindcss', 'tailwindcss-animate', 'autoprefixer', 'postcss',
    'lucide-react', 'zod', 'date-fns', 'lodash', 'lodash-es', 'server-only',
    'typescript', 'eslint', 'eslint-config-next', 'prettier',
    '@types/node', '@types/react', '@types/react-dom',
    'marked', 'react-markdown', 'remark-gfm', '@tailwindcss/typography',
    '@radix-ui/react-slot', '@radix-ui/react-dialog', '@radix-ui/react-dropdown-menu',
    '@radix-ui/react-select', '@radix-ui/react-checkbox', '@radix-ui/react-label',
    '@radix-ui/react-popover', '@radix-ui/react-tooltip', '@radix-ui/react-tabs',
    '@radix-ui/react-accordion', '@radix-ui/react-separator', '@radix-ui/react-switch',
    '@radix-ui/react-avatar', '@radix-ui/react-scroll-area', '@radix-ui/react-progress',
    'framer-motion', 'motion', 'react-hook-form', '@hookform/resolvers',
    'sonner', 'react-hot-toast', 'react-toastify',
    'next-auth', 'next-themes', 'nuqs',
    'swr', 'react-query', '@tanstack/react-query',
    '@supabase/supabase-js', '@supabase/ssr', '@supabase/auth-helpers-nextjs',
}
IGNORE_PREFIX = ('@radix-ui/', '@types/', 'tailwind', 'eslint', 'postcss', 'autoprefixer')

# ── Helpers ────────────────────────────────────────────────────────
def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return ''

def _line_of(content, pos):
    return content[:pos].count('\n') + 1

def _sha256(s):
    return hashlib.sha256(s.encode('utf-8', errors='ignore')).hexdigest()[:16]

def is_nextjs(project_path):
    app = Path(project_path) / 'app'
    if not app.exists():
        return False
    for pat in ('page.tsx', 'page.jsx', 'page.ts', 'page.js', 'route.ts', 'route.js'):
        if any(app.rglob(pat)):
            return True
    return False

def normalize_pkg(raw):
    parts = raw.split('/')
    return '/'.join(parts[:2]) if raw.startswith('@') else parts[0]

def should_ignore(pkg):
    if pkg in IGNORE_PKG:
        return True
    return any(pkg.startswith(p) for p in IGNORE_PREFIX)

PRESENTATIONAL_NAME_PARTS = frozenset({
    'header', 'footer', 'wordmark', 'brandlink', 'eyebrow', 'pageframe', 'pageheader',
    'layout', 'container', 'wrapper', 'divider', 'spacer', 'logo', 'nav', 'navbar',
    'sidebar', 'topbar', 'bottombar', 'badge', 'avatar', 'icon', 'spinner', 'skeleton',
    'brand', 'frame', 'shell', 'scaffold',
})

def _is_presentational(name, data_calls, navigations, server_actions):
    """True when a component has no data behaviour (pure UI wrapper).
    Name-pattern check only matters as a tiebreaker — zero data is required."""
    if data_calls or navigations or server_actions:
        return False
    # All components with zero detected data behaviour are presentational.
    # Name-pattern bias makes confidence explicit but doesn't change the rule.
    return True

PAGE_EXTS = ('page.tsx', 'page.jsx', 'page.ts', 'page.js')
ROUTE_EXTS = ('route.ts', 'route.js', 'route.tsx', 'route.jsx')
HTTP_METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD')
METHOD_RE = re.compile(
    r'export\s+(?:async\s+)?(?:function|const)\s+(' + '|'.join(HTTP_METHODS) + r')\b'
)
USE_SERVER_RE = re.compile(r"""^\s*["']use server["']""")
EXPORT_FN_RE = re.compile(r'export\s+(?:async\s+)?function\s+(\w+)')
IMPORT_RE = re.compile(r"""import\s+(?:.*?from\s+)?['"]([^'"]+)['"]""", re.DOTALL)
TABLE_RE = re.compile(r"""\.from\(\s*['"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]\s*\)""")
SUPABASE_OP_RE = re.compile(r"""\.from\(\s*['"]([a-zA-Z_][a-zA-Z0-9_]*)['"]\s*\)\s*\.(\w+)""")
SUPABASE_OPS = {'select', 'insert', 'update', 'delete', 'upsert', 'rpc'}

# ── ORM patterns (Prisma, Drizzle) ────────────────────────────────
PRISMA_OPS_READ  = frozenset({'findMany','findFirst','findUnique','findUniqueOrThrow',
                               'findFirstOrThrow','count','aggregate','groupBy'})
PRISMA_OPS_WRITE = frozenset({'create','createMany','createManyAndReturn',
                               'update','updateMany','delete','deleteMany','upsert'})
PRISMA_OPS = PRISMA_OPS_READ | PRISMA_OPS_WRITE
# prisma.user.findMany(  or  prisma.userSkill.create({
PRISMA_RE = re.compile(
    r'\bprisma\.([a-zA-Z][a-zA-Z0-9]*)\s*\.\s*(' + '|'.join(sorted(PRISMA_OPS)) + r')\s*[(\s{]'
)
# db.insert(users) / db.update(users) / db.delete(users)
DRIZZLE_MUTATE_RE = re.compile(
    r'\bdb\s*\.\s*(insert|update|delete)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)'
)
# db.select(...).from(users)  — unquoted var distinguishes from Supabase .from('str')
DRIZZLE_SELECT_RE = re.compile(
    r'\bdb\s*\.\s*select\b[^;]{0,200}?\.from\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)',
    re.DOTALL,
)
# db.query.users.findMany()
DRIZZLE_QUERY_RE = re.compile(
    r'\bdb\.query\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\.\s*(findMany|findFirst|findUnique)\s*[(\s{]'
)

# All DB-query kinds — used in isinstance checks throughout
DB_QUERY_KINDS = frozenset({'supabase_query', 'prisma_query', 'drizzle_query'})

def _camel_to_snake(name: str) -> str:
    """userSkill → user_skill  (Prisma model names → display table names)"""
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s).lower()

def _extract_db_queries(src: str):
    """
    Extract all database queries from source text, supporting:
      Supabase — .from('table').op()
      Prisma   — prisma.model.op()
      Drizzle  — db.insert/update/delete(table), db.select().from(table),
                 db.query.table.findMany()
    Returns list of dicts with kind in DB_QUERY_KINDS.
    """
    calls = []
    seen: set = set()

    # Supabase
    for m in SUPABASE_OP_RE.finditer(src):
        table, op = m.group(1), m.group(2).lower()
        if op in SUPABASE_OPS:
            k = (table, op)
            if k not in seen:
                seen.add(k)
                calls.append({'kind': 'supabase_query', 'table': table,
                              'operation': op, 'line': _line_of(src, m.start())})

    # Prisma (only if prisma client is referenced in the file)
    if 'prisma.' in src or 'PrismaClient' in src:
        for m in PRISMA_RE.finditer(src):
            model, op = m.group(1), m.group(2)
            table = _camel_to_snake(model)
            k = (table, op)
            if k not in seen:
                seen.add(k)
                calls.append({'kind': 'prisma_query', 'table': table,
                              'operation': op, 'line': _line_of(src, m.start())})

    # Drizzle (only if drizzle-orm is imported — guards against Array.from() etc.)
    if 'drizzle-orm' in src:
        for m in DRIZZLE_MUTATE_RE.finditer(src):
            op, table = m.group(1), m.group(2)
            k = (table, op)
            if k not in seen:
                seen.add(k)
                calls.append({'kind': 'drizzle_query', 'table': table,
                              'operation': op, 'line': _line_of(src, m.start())})
        for m in DRIZZLE_SELECT_RE.finditer(src):
            table = m.group(1)
            k = (table, 'select')
            if k not in seen:
                seen.add(k)
                calls.append({'kind': 'drizzle_query', 'table': table,
                              'operation': 'select', 'line': _line_of(src, m.start())})
        for m in DRIZZLE_QUERY_RE.finditer(src):
            table, op = m.group(1), m.group(2)
            k = (table, op)
            if k not in seen:
                seen.add(k)
                calls.append({'kind': 'drizzle_query', 'table': table,
                              'operation': op, 'line': _line_of(src, m.start())})

    return calls

# ── Caching ────────────────────────────────────────────────────────
_CACHE_BASE = Path.home() / '.claude' / 'skills' / 'codebase-visualizer' / 'cache'

def _cache_dir(project_path):
    key = _sha256(str(project_path))
    return _CACHE_BASE / key

def _cache_path(project_path, file_path):
    key = _sha256(str(file_path))
    return _cache_dir(project_path) / f'{key}.json'

def _load_cache(project_path, file_path, current_hash):
    cp = _cache_path(project_path, file_path)
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text())
        if data.get('file_hash') == current_hash:
            return data
    except Exception:
        pass
    return None

def _save_cache(project_path, file_path, data):
    cp = _cache_path(project_path, file_path)
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f'Warning: cache write failed: {e}', file=sys.stderr)

# ── Import resolution ──────────────────────────────────────────────
_SRC_EXTS = ['.tsx', '.ts', '.jsx', '.js']

def _resolve_import(imp_from, from_file, project_path):
    """Resolve a local import to an absolute file path. Returns None if not found."""
    if not imp_from:
        return None
    if imp_from.startswith('@/') or imp_from.startswith('~/'):
        base = Path(project_path) / imp_from[2:]
    elif imp_from.startswith('.'):
        base = Path(from_file).parent / imp_from
    else:
        return None
    base = base.resolve()
    # Try direct path
    for ext in [''] + _SRC_EXTS:
        candidate = Path(str(base) + ext)
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    # Try index file
    for ext in _SRC_EXTS:
        candidate = base / f'index{ext}'
        if candidate.exists():
            return str(candidate)
    return None

# ── Detect "use server" files ──────────────────────────────────────
def _find_use_server_files(project_path):
    """Return set of relative paths of files with top-level "use server"."""
    result = set()
    for d in ['app', 'lib', 'actions']:
        base = Path(project_path) / d
        if not base.exists():
            continue
        for ext in ('*.ts', '*.tsx', '*.js', '*.jsx'):
            for f in base.rglob(ext):
                content = _read(str(f))
                stripped = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
                stripped = re.sub(r'//[^\n]*', '', stripped)
                if USE_SERVER_RE.match(stripped):
                    result.add(os.path.relpath(str(f), project_path))
    return result

def _is_use_server_import(imp_from, from_file, project_path, use_server_files):
    resolved = _resolve_import(imp_from, from_file, project_path)
    if not resolved:
        return False
    rel = os.path.relpath(resolved, project_path)
    return rel in use_server_files

# ── Parse a file (with caching) ───────────────────────────────────
def _parse_file(file_path, project_path):
    src = _read(file_path)
    if not src:
        return None
    current_hash = _sha256(src)
    cached = _load_cache(project_path, file_path, current_hash)
    if cached:
        return cached
    if HAS_PARSER:
        result = _pp.parse_file(file_path, project_path)
    else:
        # Minimal fallback without page_parser
        result = _minimal_parse(file_path, project_path, src)
    _save_cache(project_path, file_path, result)
    return result

def _minimal_parse(file_path, project_path, src):
    """Regex-only fallback if page_parser.py isn't available."""
    import hashlib as _hl
    basename = Path(file_path).name
    is_page = basename in ('page.tsx', 'page.jsx', 'page.ts', 'page.js')
    route_path = ''
    if is_page and project_path:
        try:
            rel = os.path.relpath(file_path, os.path.join(project_path, 'app'))
            parts = rel.replace(os.sep, '/').split('/')[:-1]
            parts = [p for p in parts if not (p.startswith('(') and p.endswith(')'))]
            route_path = '/' + '/'.join(parts) if parts else '/'
        except Exception:
            pass
    return {
        'file': os.path.relpath(file_path, project_path),
        'file_hash': _hl.sha256(src.encode('utf-8', errors='ignore')).hexdigest()[:16],
        'is_page': is_page,
        'is_layout': basename in ('layout.tsx', 'layout.jsx'),
        'route_path': route_path,
        'imports': [],
        'components_used': [],
        'data_calls': [],
        'navigations': [],
        'server_actions_called': [],
        'parse_quality': 'low',
    }

# ── Indirect import query following ───────────────────────────────
def _collect_indirect_data_calls(file_path, project_path, use_server_files):
    """
    Follow one level of local/alias imports from file_path and collect:
      - Supabase table queries (.from('table').op())
      - External service package imports (stripe, anthropic, etc.)

    Handles the Server Component pattern where a page calls a lib function
    (e.g. listDiscountRows() from lib/stripe-promotions.ts) that internally
    uses an external service or queries Supabase directly.

    Skips: external packages, component/UI files, action files, page/route/layout files.
    Returns list of dicts with kind='supabase_query' or kind='service_call'.
    """
    content = _read(file_path)
    if not content:
        return []

    results = []
    seen_queries = set()   # (table, op, rel) — deduplicate Supabase hits
    seen_services = set()  # pkg — one service entry per lib file chain

    for m in IMPORT_RE.finditer(content):
        raw = m.group(1)
        # Only follow local / alias imports
        if not (raw.startswith('@/') or raw.startswith('~/') or raw.startswith('.')):
            continue
        # Skip component / UI directories (tracked via components_used)
        if any(seg in raw for seg in ('/components/', '/ui/', '/brand/', '/primitives')):
            continue
        # Skip assets
        if any(raw.endswith(ext) for ext in ('.css', '.svg', '.png', '.jpg', '.json')):
            continue

        resolved = _resolve_import(raw, file_path, project_path)
        if not resolved or resolved == file_path:
            continue

        rel = os.path.relpath(resolved, project_path)

        # Skip action files (tracked as server actions)
        if rel in use_server_files:
            continue
        # Skip page / route / layout / middleware files
        bname = Path(resolved).name
        if bname in ('page.tsx', 'page.ts', 'page.jsx', 'page.js',
                     'route.ts', 'route.js', 'route.tsx', 'route.jsx',
                     'layout.tsx', 'layout.ts', 'layout.jsx', 'layout.js',
                     'not-found.tsx', 'not-found.ts', 'error.tsx', 'error.ts',
                     'loading.tsx', 'loading.ts', 'middleware.ts', 'middleware.js'):
            continue

        lib_src = _read(resolved)
        if not lib_src:
            continue

        # ── DB queries (Supabase / Prisma / Drizzle) ──────────────
        for q in _extract_db_queries(lib_src):
            key = (q['table'], q['operation'], rel)
            if key in seen_queries:
                continue
            seen_queries.add(key)
            results.append({**q, 'via': rel})

        # ── External service imports inside the lib file ───────────
        for im in IMPORT_RE.finditer(lib_src):
            pkg_raw = im.group(1)
            # Must be an external package (not local)
            if (pkg_raw.startswith('.') or pkg_raw.startswith('/')
                    or pkg_raw.startswith('@/') or pkg_raw.startswith('~/')):
                continue
            pkg = normalize_pkg(pkg_raw)
            if should_ignore(pkg):
                continue
            if pkg in seen_services:
                continue
            seen_services.add(pkg)
            line = _line_of(lib_src, im.start())
            results.append({
                'kind': 'service_call',
                'service': pkg,
                'location': f'{rel}:{line}',
                'via': rel,
            })

    return results

# ── Route detection ────────────────────────────────────────────────
def _file_to_route(file_path, project_path):
    rel = os.path.relpath(file_path, os.path.join(project_path, 'app'))
    parts = rel.replace(os.sep, '/').split('/')[:-1]
    parts = [p for p in parts if not (p.startswith('(') and p.endswith(')'))]
    return '/' + '/'.join(parts) if parts else '/'

def _build_routes(project_path):
    routes = []
    app_dir = Path(project_path) / 'app'
    for ext in ROUTE_EXTS:
        for f in sorted(app_dir.rglob(ext)):
            route_path = _file_to_route(str(f), project_path)
            content = _read(str(f))
            rel = os.path.relpath(str(f), project_path)
            # Parse route file for its data calls
            sk = _parse_file(str(f), project_path)
            data_calls = sk.get('data_calls', []) if sk else []
            # Find service imports
            svc_calls = []
            for m in IMPORT_RE.finditer(content):
                raw = m.group(1)
                if raw.startswith('.') or raw.startswith('/') or raw.startswith('@/') or raw.startswith('~/'):
                    continue
                pkg = normalize_pkg(raw)
                if not should_ignore(pkg):
                    svc_calls.append({'service': f'service::{pkg}', 'location': f'{rel}:{_line_of(content, m.start())}'})
            for m in METHOD_RE.finditer(content):
                method = m.group(1)
                routes.append({
                    'id': f'route::{method}::{route_path}',
                    'method': method,
                    'path': route_path,
                    'file': rel,
                    'description': '',
                    'data_calls': data_calls,
                    'service_calls': svc_calls,
                    'called_by': [],
                })
    return routes

# ── Action detection ───────────────────────────────────────────────
def _build_actions(project_path, use_server_files):
    actions = []
    seen = set()
    for rel in sorted(use_server_files):
        full = os.path.join(project_path, rel)
        content = _read(full)
        if not content:
            continue
        for m in EXPORT_FN_RE.finditer(content):
            fn_name = m.group(1)
            nid = f'action::{rel}::{fn_name}'
            if nid not in seen:
                seen.add(nid)
                actions.append({
                    'id': nid,
                    'name': fn_name,
                    'file': rel,
                    'description': '',
                    'called_by': [],
                })
    return actions

# ── Table + service detection ──────────────────────────────────────
def _build_tables_and_services(project_path, pages, routes):
    """Build tables and services from all parsed data, with call_sites."""
    all_tables = {}   # name → {node, call_sites}
    all_services = {} # pkg → {node, call_sites}
    site_counters = {}  # table/service name → counter

    def _next_site_id(name):
        site_counters[name] = site_counters.get(name, 0) + 1
        return f'{name}::{site_counters[name]}'

    def _add_table_call(table, from_id, operation, location):
        if table not in all_tables:
            all_tables[table] = {
                'id': f'table::{table}',
                'name': table,
                'description': '',
                'call_sites': [],
                'read_count': 0,
                'write_count': 0,
            }
        entry = all_tables[table]
        site_id = _next_site_id(table)
        is_write = operation in ('insert', 'update', 'delete', 'upsert')
        entry['call_sites'].append({
            'site_id': site_id,
            'from_id': from_id,
            'operation': operation,
            'location': location,
            'summary': '',
        })
        if is_write:
            entry['write_count'] += 1
        else:
            entry['read_count'] += 1

    def _add_service_call(pkg, from_id, location):
        if pkg not in all_services:
            all_services[pkg] = {
                'id': f'service::{pkg}',
                'name': pkg,
                'description': '',
                'call_sites': [],
            }
        site_id = _next_site_id(pkg)
        all_services[pkg]['call_sites'].append({
            'site_id': site_id,
            'from_id': from_id,
            'location': location,
            'summary': '',
        })

    # From page components
    for page in pages:
        pid = page['id']
        for dc in page.get('page_level_data_calls', []):
            if dc['kind'] in DB_QUERY_KINDS:
                _add_table_call(dc['table'], pid, dc['operation'], f"{page['file']}:{dc['line']}")
        for comp in page.get('components', []):
            cid = comp['id']
            cfile = comp.get('source_file') or page['file']
            for dc in comp.get('data_calls', []):
                if dc['kind'] in DB_QUERY_KINDS:
                    _add_table_call(dc['table'], cid, dc['operation'], f"{cfile}:{dc['line']}")

    # From pages (indirect service calls via lib imports)
    for page in pages:
        pid = page['id']
        for sc in page.get('page_service_calls', []):
            _add_service_call(sc['service'], pid, sc['location'])

    # From routes
    for route in routes:
        rid = route['id']
        for dc in route.get('data_calls', []):
            if dc['kind'] in DB_QUERY_KINDS:
                _add_table_call(dc['table'], rid, dc['operation'], f"{route['file']}:{dc['line']}")
        for sc in route.get('service_calls', []):
            pkg = sc['service'].replace('service::', '')
            _add_service_call(pkg, rid, sc['location'])

    # Also scan lib/components for services
    for d in ['lib', 'components', 'app']:
        base = Path(project_path) / d
        if not base.exists():
            continue
        for ext in ('*.ts', '*.tsx', '*.js', '*.jsx'):
            for f in sorted(base.rglob(ext)):
                content = _read(str(f))
                if not content:
                    continue
                rel = os.path.relpath(str(f), project_path)
                for m in IMPORT_RE.finditer(content):
                    raw = m.group(1)
                    if raw.startswith('.') or raw.startswith('/') or raw.startswith('@/') or raw.startswith('~/'):
                        continue
                    pkg = normalize_pkg(raw)
                    if not should_ignore(pkg):
                        if pkg not in all_services:
                            all_services[pkg] = {
                                'id': f'service::{pkg}',
                                'name': pkg,
                                'description': '',
                                'call_sites': [],
                            }

    return list(all_tables.values()), list(all_services.values())

# ── Edge building ──────────────────────────────────────────────────
def _build_edges(pages, routes, actions, tables, services):
    edges = []
    seen = set()

    route_lookup = {}  # (method, path) → id
    for r in routes:
        route_lookup[(r['method'], r['path'])] = r['id']

    action_lookup = {}  # (file, name) → id
    for a in actions:
        action_lookup[(a['file'], a['name'])] = a['id']
    use_server_map = {}  # file → [action_ids]
    for a in actions:
        use_server_map.setdefault(a['file'], []).append(a['id'])

    table_ids = {t['name']: t['id'] for t in tables}
    service_ids = {s['name']: s['id'] for s in services}

    def add(from_id, to_id, etype, location, confidence='high', **extra):
        key = (from_id, to_id, etype)
        if key not in seen:
            seen.add(key)
            e = {'from_id': from_id, 'to_id': to_id, 'type': etype,
                 'location': location, 'confidence': confidence}
            e.update(extra)
            edges.append(e)

    def _find_route(url, method):
        rid = route_lookup.get((method, url))
        if rid:
            return rid
        for m2 in HTTP_METHODS:
            rid = route_lookup.get((m2, url))
            if rid:
                return rid
        return None

    for page in pages:
        pid = page['id']
        page_file = page['file']

        # Page-level data calls
        for dc in page.get('page_level_data_calls', []):
            if dc['kind'] in DB_QUERY_KINDS:
                tid = table_ids.get(dc['table'])
                if tid:
                    add(pid, tid, 'queries', f"{page_file}:{dc['line']}", operation=dc['operation'])
            elif dc['kind'] == 'fetch':
                rid = _find_route(dc.get('url', ''), dc.get('method', 'GET'))
                if rid:
                    add(pid, rid, 'fetches', f"{page_file}:{dc['line']}")

        # Page-level server actions
        for sa in page.get('page_level_navigations', []):
            pass  # navigations handled below

        # Component data calls
        for comp in page.get('components', []):
            cid = comp['id']
            cfile = comp.get('source_file') or page_file

            for dc in comp.get('data_calls', []):
                if dc['kind'] in DB_QUERY_KINDS:
                    tid = table_ids.get(dc['table'])
                    if tid:
                        add(cid, tid, 'queries', f"{cfile}:{dc['line']}", operation=dc['operation'])
                elif dc['kind'] == 'fetch':
                    rid = _find_route(dc.get('url', ''), dc.get('method', 'GET'))
                    if rid:
                        add(cid, rid, 'fetches', f"{cfile}:{dc['line']}")

            for nav in comp.get('navigations', []):
                target = nav.get('href') or nav.get('target', '')
                if target:
                    target_pid = f'page::{target}'
                    # Check if target page exists
                    add(cid, target_pid, 'navigates', f"{cfile}:{nav['line']}", confidence='medium')

            for sa in comp.get('server_actions_called', []):
                imp = sa.get('from_import', '')
                for ext_try in ['', '.ts', '.tsx', '.js', '.jsx']:
                    cand = imp.lstrip('@/').lstrip('~/') + ext_try
                    if cand in use_server_map:
                        for aid in use_server_map[cand]:
                            add(cid, aid, 'invokes', f"{cfile}:{sa['line']}")
                        break

    # Page → service edges (from indirect lib imports)
    for page in pages:
        pid = page['id']
        for sc in page.get('page_service_calls', []):
            sid = service_ids.get(sc['service'])
            if sid:
                add(pid, sid, 'calls', sc['location'])

    # Route → service edges
    for route in routes:
        rid = route['id']
        for sc in route.get('service_calls', []):
            pkg = sc['service'].replace('service::', '')
            sid = service_ids.get(pkg)
            if sid:
                add(rid, sid, 'calls', sc['location'])
        for dc in route.get('data_calls', []):
            if dc['kind'] in DB_QUERY_KINDS:
                tid = table_ids.get(dc['table'])
                if tid:
                    add(rid, tid, 'queries', f"{route['file']}:{dc['line']}", operation=dc['operation'])

    return edges

# ── User journey ───────────────────────────────────────────────────
def _build_user_journey(pages, edges):
    page_ids = {p['id'] for p in pages}
    # Build graph of page→page navigations
    out_edges = {}  # page_id → [target_page_id]
    in_count = {}
    for p in pages:
        out_edges[p['id']] = []
        in_count[p['id']] = 0

    for e in edges:
        if e['type'] == 'navigates' and e['to_id'] in page_ids:
            # from_id might be a comp:: id, find its page
            from_page = None
            if e['from_id'].startswith('page::'):
                from_page = e['from_id']
            elif e['from_id'].startswith('comp::'):
                # comp::{route}::{name}::{line} → page::{route}
                parts = e['from_id'].split('::')
                if len(parts) >= 2:
                    from_page = f'page::/{parts[1]}' if not parts[1].startswith('/') else f'page::{parts[1]}'
            if from_page and from_page in page_ids and e['to_id'] in page_ids and e['to_id'] != from_page:
                if e['to_id'] not in out_edges.get(from_page, []):
                    out_edges.setdefault(from_page, []).append(e['to_id'])
                    in_count[e['to_id']] = in_count.get(e['to_id'], 0) + 1

    # BFS layers
    entry_points = [pid for pid in page_ids if in_count.get(pid, 0) == 0]
    journey = []
    for ep in sorted(entry_points):
        for target in out_edges.get(ep, []):
            journey.append({'from': ep, 'to': target, 'via': 'page-level'})

    return journey

# ── Layer ordering ─────────────────────────────────────────────────
def _compute_page_layers(pages, edges):
    """Return dict {page_id: layer} where layer 0 = entry point."""
    page_ids = {p['id'] for p in pages}
    in_count = {pid: 0 for pid in page_ids}
    out_map = {pid: [] for pid in page_ids}

    for e in edges:
        if e['type'] == 'navigates' and e['to_id'] in page_ids:
            from_page = None
            if e['from_id'].startswith('page::'):
                from_page = e['from_id']
            elif e['from_id'].startswith('comp::'):
                parts = e['from_id'].split('::')
                if len(parts) >= 2:
                    route = parts[1]
                    from_page = f'page::/{route}' if not route.startswith('/') else f'page::{route}'
            if from_page and from_page in page_ids and e['to_id'] in page_ids and e['to_id'] != from_page:
                out_map[from_page].append(e['to_id'])
                in_count[e['to_id']] += 1

    layers = {}
    queue = [pid for pid in page_ids if in_count.get(pid, 0) == 0]
    for pid in sorted(queue):
        layers[pid] = 0

    visited = set(queue)
    while queue:
        nxt = []
        for pid in queue:
            for target in out_map.get(pid, []):
                if target not in visited:
                    visited.add(target)
                    layers[target] = layers.get(pid, 0) + 1
                    nxt.append(target)
        queue = nxt

    # Orphans (no in or out navigation)
    for pid in page_ids:
        if pid not in layers:
            layers[pid] = 999

    return layers

# ── Main orchestration ─────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print('Usage: flow_analyzer.py <project_path>', file=sys.stderr)
        sys.exit(1)

    project_path = os.path.abspath(sys.argv[1])

    if not is_nextjs(project_path):
        print(json.dumps({'is_nextjs': False}))
        return

    print('Detecting use-server files…', file=sys.stderr)
    use_server_files = _find_use_server_files(project_path)

    # ── Parse all page files ───────────────────────────────────────
    app_dir = Path(project_path) / 'app'
    page_files = []
    for ext in PAGE_EXTS:
        page_files.extend(sorted(app_dir.rglob(ext)))

    print(f'Parsing {len(page_files)} page files…', file=sys.stderr)

    pages = []
    seen_routes = set()

    for pf in page_files:
        pf_str = str(pf)
        sk = _parse_file(pf_str, project_path)
        if not sk:
            continue

        route_path = sk.get('route_path') or '/'
        pid = f'page::{route_path}'
        if pid in seen_routes:
            continue
        seen_routes.add(pid)

        components = []
        seen_comp_names = set()

        for comp_used in sk.get('components_used', []):
            name = comp_used['name']
            line = comp_used['line']
            from_imp = comp_used.get('from_import', 'inline')

            # Deduplicate same component used multiple times on same page
            if (name, line) in seen_comp_names:
                continue
            seen_comp_names.add((name, line))

            comp_data_calls = []
            comp_navs = []
            comp_actions = []
            source_file = ''

            if from_imp and from_imp != 'inline':
                resolved = _resolve_import(from_imp, pf_str, project_path)
                if resolved and resolved != pf_str:
                    comp_sk = _parse_file(resolved, project_path)
                    if comp_sk:
                        comp_indirect = _collect_indirect_data_calls(resolved, project_path, use_server_files)
                        comp_data_calls = comp_sk.get('data_calls', []) + [
                            x for x in comp_indirect if x['kind'] == 'supabase_query'
                        ]
                        comp_navs = comp_sk.get('navigations', [])
                        comp_actions = [
                            sa for sa in comp_sk.get('server_actions_called', [])
                            if _is_use_server_import(sa['from_import'], resolved, project_path, use_server_files)
                        ]
                        source_file = comp_sk.get('file', '')

            comp_id = f'comp::{route_path}::{name}::{line}'
            presentational = _is_presentational(name, comp_data_calls, comp_navs, comp_actions)

            components.append({
                'id': comp_id,
                'name': name,
                'source_file': source_file,
                'description': '',
                'data_summary': '',
                'data_calls': comp_data_calls,
                'navigations': comp_navs,
                'server_actions_called': comp_actions,
                'is_presentational': presentational,
            })

        # Page-level server actions
        page_actions = [
            sa for sa in sk.get('server_actions_called', [])
            if _is_use_server_import(sa['from_import'], pf_str, project_path, use_server_files)
        ]

        # Merge direct + indirect (lib-import) data calls and service calls
        page_indirect = _collect_indirect_data_calls(pf_str, project_path, use_server_files)
        page_data_calls = sk.get('data_calls', []) + [x for x in page_indirect if x['kind'] == 'supabase_query']
        page_service_calls = [x for x in page_indirect if x['kind'] == 'service_call']

        pages.append({
            'id': pid,
            'route_path': route_path,
            'file': sk.get('file', os.path.relpath(pf_str, project_path)),
            'description': '',
            'components': components,
            'page_level_data_calls': page_data_calls,
            'page_service_calls': page_service_calls,
            'page_level_navigations': sk.get('navigations', []),
            'page_level_actions': page_actions,
            'parse_quality': sk.get('parse_quality', 'medium'),
        })

    # ── Build routes ───────────────────────────────────────────────
    print('Building routes…', file=sys.stderr)
    routes = _build_routes(project_path)

    # ── Build actions ──────────────────────────────────────────────
    actions = _build_actions(project_path, use_server_files)

    # ── Build tables + services ────────────────────────────────────
    print('Building tables and services…', file=sys.stderr)
    tables, services = _build_tables_and_services(project_path, pages, routes)

    # ── Build edges ────────────────────────────────────────────────
    print('Building edges…', file=sys.stderr)
    edges = _build_edges(pages, routes, actions, tables, services)

    # ── Wire called_by on routes/actions ──────────────────────────
    for r in routes:
        r['called_by'] = [e['from_id'] for e in edges if e['to_id'] == r['id'] and e['type'] == 'fetches']
    for a in actions:
        a['called_by'] = [e['from_id'] for e in edges if e['to_id'] == a['id'] and e['type'] == 'invokes']

    # ── Compute page layers (journey ordering) ─────────────────────
    layers = _compute_page_layers(pages, edges)
    for p in pages:
        p['journey_layer'] = layers.get(p['id'], 999)

    # Sort pages by layer then route_path
    pages.sort(key=lambda p: (p['journey_layer'], p['route_path']))

    # ── User journey ───────────────────────────────────────────────
    user_journey = _build_user_journey(pages, edges)

    # ── Remove internal fields ─────────────────────────────────────
    for p in pages:
        p.pop('page_level_actions', None)
        p.pop('page_service_calls', None)

    stats = {
        'pages': len(pages),
        'components': sum(len(p['components']) for p in pages),
        'routes': len(routes),
        'actions': len(actions),
        'services': len(services),
        'tables': len(tables),
        'edges': len(edges),
    }

    print(
        f'Detected: {stats["pages"]} pages, {stats["components"]} components, '
        f'{stats["routes"]} routes, {stats["actions"]} actions, '
        f'{stats["services"]} services, {stats["tables"]} tables, {stats["edges"]} edges',
        file=sys.stderr
    )

    out = {
        'is_nextjs': True,
        'pages': pages,
        'tables': tables,
        'services': services,
        'actions': actions,
        'routes': routes,
        'edges': edges,
        'user_journey': user_journey,
        'stats': stats,
    }
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
