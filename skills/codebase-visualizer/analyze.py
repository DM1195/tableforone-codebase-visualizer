import subprocess
import json
import sys
import os
from pathlib import Path

FUNCTION_KINDS = {
    'function', 'method', 'member', 'subroutine', 'procedure',
    'func', 'f', 'm', 'sub',
}

CTAGS_BIN = '/opt/homebrew/bin/ctags' if Path('/opt/homebrew/bin/ctags').exists() else 'ctags'
RG_BIN = '/opt/homebrew/bin/rg' if Path('/opt/homebrew/bin/rg').exists() else 'rg'


EXCLUDE_DIRS = [
    'node_modules', '.git', 'dist', 'build', '.next', '.nuxt',
    '__pycache__', '.venv', 'venv', 'env', '.env', 'vendor',
    'coverage', '.nyc_output', 'out', '.cache', 'tmp', '.turbo',
]


def run_ctags(project_path):
    exclude_args = [f'--exclude={d}' for d in EXCLUDE_DIRS]
    result = subprocess.run(
        [CTAGS_BIN, '--output-format=json', '--fields=+n', '-R'] + exclude_args + ['.'],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        print(f'ctags failed: {result.stderr}', file=sys.stderr)
        sys.exit(1)
    fns = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        try:
            tag = json.loads(line)
        except json.JSONDecodeError:
            continue
        if tag.get('_type') != 'tag':
            continue
        if tag.get('kind', '').lower() not in FUNCTION_KINDS:
            continue
        path = tag['path']
        name = tag['name']
        line_no = tag.get('line', 0)
        fns.append({
            'id': f'{path}::{name}::{line_no}',
            'name': name,
            'file': path,
            'line': line_no,
            'language': tag.get('language', 'unknown'),
            'callers': [],
            'caller_fns': [],
            'calls': [],
            'is_dead_code': False,
            'linter_issues': [],
            'description': '',
            'bugs': [],
            'cursor_prompt': '',
            'notes': [],
        })
    return fns


def find_callers(fn_name, project_path, fn_file, fn_line):
    result = subprocess.run(
        [RG_BIN, '--json', '--word-regexp', fn_name, '.', '--glob=!node_modules/**', '--glob=!.git/**', '--glob=!dist/**', '--glob=!.next/**', '--glob=!build/**'],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    # rg exits 1 when no matches found — that's fine; only fail on 2+
    if result.returncode >= 2:
        print(f'rg warning for {fn_name}: {result.stderr}', file=sys.stderr)
        return []
    callers = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        try:
            match = json.loads(line)
        except json.JSONDecodeError:
            continue
        if match.get('type') != 'match':
            continue
        data = match['data']
        caller_file = data['path']['text'].lstrip('./')
        caller_line = data['line_number']
        if caller_file == fn_file and caller_line == fn_line:
            continue
        callers.append({'file': caller_file, 'line': caller_line})
    return callers


def resolve_caller_fns(fns):
    by_file = {}
    for fn in fns:
        by_file.setdefault(fn['file'], []).append(fn)
    for file_fns in by_file.values():
        file_fns.sort(key=lambda f: f['line'])

    for fn in fns:
        seen_ids = set()
        caller_fn_objects = []
        for caller in fn['callers']:
            file_fns = by_file.get(caller['file'], [])
            containing = None
            for f in file_fns:
                if f['line'] <= caller['line']:
                    containing = f
                else:
                    break
            if containing and containing['name'] != fn['name'] and containing['id'] not in seen_ids:
                seen_ids.add(containing['id'])
                caller_fn_objects.append({
                    'id': containing['id'],
                    'name': containing['name'],
                    'file': containing['file'],
                })
        fn['caller_fns'] = caller_fn_objects


def derive_calls(fns):
    """Inverse of caller_fns: if A is in B's caller_fns, add B to A's calls."""
    by_id = {fn['id']: fn for fn in fns}
    for fn in fns:
        for caller_obj in fn['caller_fns']:
            caller = by_id.get(caller_obj['id'])
            if caller:
                existing_ids = {c['id'] for c in caller['calls']}
                if fn['id'] not in existing_ids:
                    caller['calls'].append({
                        'id': fn['id'],
                        'name': fn['name'],
                        'file': fn['file'],
                    })


def build_edges(fns):
    seen = set()
    edges = []
    for fn in fns:
        for caller_obj in fn['caller_fns']:
            key = (caller_obj['id'], fn['id'])
            if key not in seen:
                seen.add(key)
                edges.append({'from_id': caller_obj['id'], 'to_id': fn['id']})
    return edges


def build_files_metadata(fns):
    files = {}
    for fn in fns:
        path = fn['file']
        if path not in files:
            files[path] = {'path': path, 'fn_count': 0, 'dead_count': 0, 'issue_count': 0}
        files[path]['fn_count'] += 1
        if fn['is_dead_code']:
            files[path]['dead_count'] += 1
        files[path]['issue_count'] += len(fn['linter_issues'])
    return sorted(files.values(), key=lambda f: f['path'])


def flag_dead_code(fns):
    for fn in fns:
        fn['is_dead_code'] = len(fn['callers']) == 0


def detect_linters(project_path):
    p = Path(project_path)
    linters = []
    eslint_patterns = [
        '.eslintrc', '.eslintrc.js', '.eslintrc.json',
        '.eslintrc.yml', 'eslint.config.js', 'eslint.config.mjs',
    ]
    if any((p / name).exists() for name in eslint_patterns):
        linters.append('eslint')
    if (p / '.pylintrc').exists():
        linters.append('pylint')
    if (p / 'setup.cfg').exists():
        content = (p / 'setup.cfg').read_text()
        if '[pylint' in content:
            linters.append('pylint')
    return list(set(linters))


def run_eslint(project_path):
    result = subprocess.run(
        ['npx', '--no', 'eslint', '--format=json', '.'],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    issues = []
    try:
        data = json.loads(result.stdout)
        for file_result in data:
            rel_path = os.path.relpath(file_result['filePath'], project_path)
            for msg in file_result.get('messages', []):
                issues.append({
                    'file': rel_path,
                    'line': msg.get('line', 0),
                    'message': msg['message'],
                    'severity': 'error' if msg.get('severity') == 2 else 'warning',
                })
    except (json.JSONDecodeError, KeyError):
        pass
    return issues


def run_pylint(project_path):
    result = subprocess.run(
        ['pylint', '--output-format=json', '--recursive=y', '.'],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    issues = []
    try:
        data = json.loads(result.stdout)
        for item in data:
            issues.append({
                'file': item.get('path', ''),
                'line': item.get('line', 0),
                'message': item.get('message', ''),
                'severity': item.get('type', 'warning'),
            })
    except (json.JSONDecodeError, KeyError):
        pass
    return issues


def assign_linter_issues(fns, linter_issues):
    by_file = {}
    for fn in fns:
        by_file.setdefault(fn['file'], []).append(fn)
    for file_fns in by_file.values():
        file_fns.sort(key=lambda f: f['line'])

    for issue in linter_issues:
        file_fns = by_file.get(issue['file'], [])
        best = None
        for fn in file_fns:
            if fn['line'] <= issue['line']:
                best = fn
            else:
                break
        if best is not None:
            best['linter_issues'].append(issue['message'])


def main():
    if len(sys.argv) < 2:
        print('Usage: analyze.py <project_path>', file=sys.stderr)
        sys.exit(1)

    project_path = os.path.abspath(sys.argv[1])
    fns = run_ctags(project_path)

    for fn in fns:
        fn['callers'] = find_callers(fn['name'], project_path, fn['file'], fn['line'])

    resolve_caller_fns(fns)
    flag_dead_code(fns)
    derive_calls(fns)

    linter_issues = []
    for linter in detect_linters(project_path):
        if linter == 'eslint':
            linter_issues.extend(run_eslint(project_path))
        elif linter == 'pylint':
            linter_issues.extend(run_pylint(project_path))

    assign_linter_issues(fns, linter_issues)

    edges = build_edges(fns)
    files_meta = build_files_metadata(fns)

    print(json.dumps({
        'project_path': project_path,
        'fns': fns,
        'edges': edges,
        'files': files_meta,
        'total': len(fns),
    }, indent=2))


if __name__ == '__main__':
    main()
