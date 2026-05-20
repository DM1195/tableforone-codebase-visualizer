"""
page_parser.py — structural parser for Next.js page/component files.
Uses tree-sitter for JSX/TSX; falls back to regex if not installed.
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# ── tree-sitter setup ──────────────────────────────────────────────
HAS_TS = False
_TSX_LANG = None
_TS_LANG = None

def _init_tree_sitter():
    global HAS_TS, _TSX_LANG, _TS_LANG
    try:
        import tree_sitter_typescript as tst
        from tree_sitter import Language, Parser
        _TSX_LANG = Language(tst.language_tsx())
        _TS_LANG  = Language(tst.language_typescript())
        HAS_TS = True
    except ImportError:
        pass
    except Exception as e:
        print(f'Warning: tree-sitter init failed: {e}', file=sys.stderr)

_init_tree_sitter()

# ── helpers ────────────────────────────────────────────────────────
def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return ''

def _sha256(text):
    return hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()[:16]

def _line_of(src, pos):
    return src[:pos].count('\n') + 1

def _is_local(src):
    return src.startswith('.') or src.startswith('@/') or src.startswith('~/')

def _route_from_path(file_path, project_path):
    """app/(group)/foo/[id]/page.tsx → /foo/[id]"""
    try:
        rel = os.path.relpath(file_path, os.path.join(project_path, 'app'))
        parts = rel.replace(os.sep, '/').split('/')[:-1]
        parts = [p for p in parts if not (p.startswith('(') and p.endswith(')'))]
        return '/' + '/'.join(parts) if parts else '/'
    except Exception:
        return '/'

# ── regex patterns ─────────────────────────────────────────────────
_IMPORT_RE = re.compile(
    r"""import\s+(?:type\s+)?(?:\{([^}]*)\}|(\w+)(?:\s*,\s*\{([^}]*)\})?)\s*from\s*['"]([^'"]+)['"]""",
    re.DOTALL | re.MULTILINE,
)
_IMPORT_NS_RE = re.compile(
    r"""import\s+(?:type\s+)?\*\s+as\s+(\w+)\s+from\s*['"]([^'"]+)['"]""",
)
_SUPABASE_RE = re.compile(
    r"""\.from\(\s*['"]([a-zA-Z_][a-zA-Z0-9_]*)['"]\s*\)\s*\.(\w+)"""
)
_PRISMA_OPS = frozenset({'findMany','findFirst','findUnique','findUniqueOrThrow',
                          'findFirstOrThrow','count','aggregate','groupBy',
                          'create','createMany','createManyAndReturn',
                          'update','updateMany','delete','deleteMany','upsert'})
_PRISMA_RE = re.compile(
    r'\bprisma\.([a-zA-Z][a-zA-Z0-9]*)\s*\.\s*(' + '|'.join(sorted(_PRISMA_OPS)) + r')\s*[(\s{]'
)
_DRIZZLE_MUTATE_RE = re.compile(
    r'\bdb\s*\.\s*(insert|update|delete)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)'
)
_DRIZZLE_SELECT_RE = re.compile(
    r'\bdb\s*\.\s*select\b[^;]{0,200}?\.from\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)',
    re.DOTALL,
)
_DRIZZLE_QUERY_RE = re.compile(
    r'\bdb\.query\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\.\s*(findMany|findFirst|findUnique)\s*[(\s{]'
)

def _camel_to_snake(name: str) -> str:
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s).lower()
_FETCH_RE = re.compile(r"""fetch\s*\(\s*[`'"](/[^`'"?#\s]+)[`'"]""")
_FETCH_METHOD_RE = re.compile(r"""method\s*:\s*['"](\w+)['"]""")
_SWR_RE = re.compile(r"""useSWR(Mutation)?\s*\(\s*[`'"](/[^`'"?#\s]+)[`'"]""")
_HREF_RE = re.compile(r"""href\s*=\s*['"](/[^'"?#]+)['"]""")
_HREF_EXPR_RE = re.compile(r"""href\s*=\s*\{[^}]*['"](/[^'"?#]+)['"]""")
_ROUTER_RE = re.compile(r"""router\.(?:push|replace)\s*\(\s*['"](/[^'"?#]+)['"]""")
_JSX_COMP_RE = re.compile(r"""<([A-Z][a-zA-Z0-9_]*)[\s/>]""")


# ── import extraction ─────────────────────────────────────────────
def _extract_imports(src):
    imports = []
    seen = set()

    def add(name, from_src):
        key = (name, from_src)
        if key not in seen and name:
            seen.add(key)
            imports.append({'name': name, 'from': from_src, 'is_local': _is_local(from_src)})

    for m in _IMPORT_RE.finditer(src):
        named_str, default_name, extra_named, from_src = m.groups()
        if default_name:
            add(default_name.strip(), from_src)
        for nstr in [named_str, extra_named]:
            if not nstr:
                continue
            for part in nstr.split(','):
                part = part.strip()
                if not part:
                    continue
                # handle "X as Y" — take X (original export name)
                name = re.split(r'\s+as\s+', part)[0].strip().lstrip('type').strip()
                add(name, from_src)

    for m in _IMPORT_NS_RE.finditer(src):
        add(m.group(1), m.group(2))

    return imports


# ── data calls extraction ─────────────────────────────────────────
SUPABASE_OPS = {'select', 'insert', 'update', 'delete', 'upsert', 'rpc'}

def _extract_data_calls(src):
    calls = []
    seen: set = set()

    # ── Supabase ──────────────────────────────────────────────────
    for m in _SUPABASE_RE.finditer(src):
        table, op = m.group(1), m.group(2).lower()
        if op not in SUPABASE_OPS:
            continue
        k = (table, op)
        if k not in seen:
            seen.add(k)
            raw = src[m.start():m.start() + 120].replace('\n', ' ')
            calls.append({'kind': 'supabase_query', 'table': table, 'operation': op,
                          'line': _line_of(src, m.start()), 'raw': raw[:120]})

    # ── Prisma ────────────────────────────────────────────────────
    if 'prisma.' in src or 'PrismaClient' in src:
        for m in _PRISMA_RE.finditer(src):
            model, op = m.group(1), m.group(2)
            table = _camel_to_snake(model)
            k = (table, op)
            if k not in seen:
                seen.add(k)
                calls.append({'kind': 'prisma_query', 'table': table, 'operation': op,
                              'line': _line_of(src, m.start()), 'raw': ''})

    # ── Drizzle ───────────────────────────────────────────────────
    if 'drizzle-orm' in src:
        for m in _DRIZZLE_MUTATE_RE.finditer(src):
            op, table = m.group(1), m.group(2)
            k = (table, op)
            if k not in seen:
                seen.add(k)
                calls.append({'kind': 'drizzle_query', 'table': table, 'operation': op,
                              'line': _line_of(src, m.start()), 'raw': ''})
        for m in _DRIZZLE_SELECT_RE.finditer(src):
            table = m.group(1)
            k = (table, 'select')
            if k not in seen:
                seen.add(k)
                calls.append({'kind': 'drizzle_query', 'table': table, 'operation': 'select',
                              'line': _line_of(src, m.start()), 'raw': ''})
        for m in _DRIZZLE_QUERY_RE.finditer(src):
            table, op = m.group(1), m.group(2)
            k = (table, op)
            if k not in seen:
                seen.add(k)
                calls.append({'kind': 'drizzle_query', 'table': table, 'operation': op,
                              'line': _line_of(src, m.start()), 'raw': ''})

    for m in _FETCH_RE.finditer(src):
        url = m.group(1)
        window = src[m.start():m.start() + 500]
        meth_m = _FETCH_METHOD_RE.search(window)
        method = meth_m.group(1).upper() if meth_m else 'GET'
        raw = src[m.start():m.start() + 80].replace('\n', ' ')
        calls.append({
            'kind': 'fetch',
            'url': url,
            'method': method,
            'line': _line_of(src, m.start()),
            'raw': raw[:120],
        })

    for m in _SWR_RE.finditer(src):
        url = m.group(2)
        is_mutation = bool(m.group(1))
        raw = src[m.start():m.start() + 80].replace('\n', ' ')
        calls.append({
            'kind': 'fetch',
            'url': url,
            'method': 'POST' if is_mutation else 'GET',
            'line': _line_of(src, m.start()),
            'raw': raw[:120],
        })

    return calls


# ── navigation extraction ─────────────────────────────────────────
def _extract_navigations(src):
    navs = []
    seen = set()

    def add(kind, target, line):
        key = (kind, target)
        if key not in seen:
            seen.add(key)
            field = 'href' if kind == 'link' else 'target'
            navs.append({'kind': kind, field: target, 'line': line, 'from_component_name': ''})

    for m in list(_HREF_RE.finditer(src)) + list(_HREF_EXPR_RE.finditer(src)):
        add('link', m.group(1), _line_of(src, m.start()))
    for m in _ROUTER_RE.finditer(src):
        add('router_push', m.group(1), _line_of(src, m.start()))

    return navs


# ── component extraction via tree-sitter ──────────────────────────
def _extract_components_ts(src, imports):
    from tree_sitter import Parser

    lang = _TSX_LANG
    parser = Parser(lang)
    tree = parser.parse(src.encode('utf-8', errors='replace'))
    import_map = {imp['name']: imp['from'] for imp in imports}
    components = []
    seen = set()

    def walk(node):
        if node.type == 'jsx_opening_element':
            name_node = node.child_by_field_name('name')
            if name_node:
                name = src[name_node.start_byte:name_node.end_byte]
                if name and name[0].isupper() and name not in ('Fragment',):
                    line = name_node.start_point[0] + 1
                    key = (name, line)
                    if key not in seen:
                        seen.add(key)
                        props = []
                        for child in node.children:
                            if child.type == 'jsx_attribute':
                                aname = child.child_by_field_name('name')
                                if aname:
                                    props.append(src[aname.start_byte:aname.end_byte])
                        components.append({
                            'name': name,
                            'from_import': import_map.get(name, 'inline'),
                            'line': line,
                            'props_passed': props,
                        })
        for child in node.children:
            walk(child)

    try:
        walk(tree.root_node)
        quality = 'high' if not tree.root_node.has_error else 'medium'
    except RecursionError:
        quality = 'medium'
        components = _extract_components_regex(src, imports)

    return components, quality


def _extract_components_regex(src, imports):
    import_map = {imp['name']: imp['from'] for imp in imports}
    components = []
    seen = set()
    for m in _JSX_COMP_RE.finditer(src):
        name = m.group(1)
        if name in ('Fragment', 'React', 'Suspense', 'ErrorBoundary'):
            continue
        line = _line_of(src, m.start())
        key = (name, line)
        if key not in seen:
            seen.add(key)
            components.append({
                'name': name,
                'from_import': import_map.get(name, 'inline'),
                'line': line,
                'props_passed': [],
            })
    return components


# ── server actions extraction ─────────────────────────────────────
def _extract_server_actions(src, imports):
    local_imports = {imp['name']: imp['from'] for imp in imports if imp['is_local']}
    if not local_imports:
        return []
    pattern = r'\b(' + '|'.join(re.escape(n) for n in local_imports) + r')\s*\('
    fn_call_re = re.compile(pattern, re.MULTILINE)
    actions = []
    seen = set()
    for m in fn_call_re.finditer(src):
        name = m.group(1)
        key = name
        if key not in seen:
            seen.add(key)
            actions.append({
                'name': name,
                'from_import': local_imports[name],
                'line': _line_of(src, m.start()),
            })
    return actions


# ── main parse function ───────────────────────────────────────────
def parse_file(file_path, project_path=''):
    src = _read(file_path)
    if not src:
        return {
            'file': os.path.relpath(file_path, project_path) if project_path else file_path,
            'file_hash': '',
            'is_page': False,
            'is_layout': False,
            'route_path': '',
            'imports': [],
            'components_used': [],
            'data_calls': [],
            'navigations': [],
            'server_actions_called': [],
            'parse_quality': 'failed',
            'error': 'Cannot read file',
        }

    file_hash = _sha256(src)
    basename = Path(file_path).name
    is_page = basename in ('page.tsx', 'page.jsx', 'page.ts', 'page.js')
    is_layout = basename in ('layout.tsx', 'layout.jsx', 'layout.ts', 'layout.js')

    route_path = ''
    if (is_page or is_layout) and project_path:
        route_path = _route_from_path(file_path, project_path)

    rel_path = os.path.relpath(file_path, project_path) if project_path else file_path

    imports = _extract_imports(src)
    data_calls = _extract_data_calls(src)
    navigations = _extract_navigations(src)
    server_actions = _extract_server_actions(src, imports)

    if HAS_TS:
        try:
            components, parse_quality = _extract_components_ts(src, imports)
        except Exception as e:
            print(f'Warning: tree-sitter parse failed for {rel_path}: {e}', file=sys.stderr)
            components = _extract_components_regex(src, imports)
            parse_quality = 'medium'
    else:
        components = _extract_components_regex(src, imports)
        parse_quality = 'medium'

    return {
        'file': rel_path,
        'file_hash': file_hash,
        'is_page': is_page,
        'is_layout': is_layout,
        'route_path': route_path,
        'imports': imports,
        'components_used': components,
        'data_calls': data_calls,
        'navigations': navigations,
        'server_actions_called': server_actions,
        'parse_quality': parse_quality,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: page_parser.py <file> [project_path]', file=sys.stderr)
        sys.exit(1)
    result = parse_file(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else '')
    print(json.dumps(result, indent=2))
