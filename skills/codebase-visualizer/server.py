import http.server
import json
import os
import re
import sys
import webbrowser
from pathlib import Path


def load_data(data_file):
    with open(data_file) as f:
        return json.load(f)


def save_data(data_file, data):
    with open(data_file, 'w') as f:
        json.dump(data, f, indent=2)


def parse_trace(trace_text, fns):
    patterns = [
        re.compile(r'File "([^"]+)", line (\d+)'),
        re.compile(r'at \w+ \(([^:]+):(\d+):\d+\)'),
        re.compile(r'at ([^(]+):(\d+):\d+'),
    ]
    trace_refs = []
    for pattern in patterns:
        for m in pattern.finditer(trace_text):
            file_path = m.group(1)
            line = int(m.group(2))
            trace_refs.append((os.path.basename(file_path), line))

    highlighted = set()
    by_file = {}
    for fn in fns:
        by_file.setdefault(os.path.basename(fn['file']), []).append(fn)

    for filename, line in trace_refs:
        file_fns = sorted(by_file.get(filename, []), key=lambda f: f['line'])
        if not file_fns:
            continue
        best = file_fns[0]
        for fn in file_fns:
            if fn['line'] <= line:
                best = fn
            else:
                break
        highlighted.add(best.get('id', best['name']))

    return list(highlighted)


def make_handler(data_file, flow_file, skill_dir):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ('/', '/index.html'):
                self._serve_file(skill_dir / 'index.html', 'text/html')
            elif self.path == '/data.json':
                data = load_data(data_file)
                self._send_json(data, no_cache=True)
            elif self.path == '/flow.json':
                if flow_file and Path(flow_file).exists():
                    data = load_data(flow_file)
                else:
                    data = {'is_nextjs': False, 'nodes': [], 'edges': []}
                self._send_json(data, no_cache=True)
            else:
                self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))

            if self.path == '/note':
                data = load_data(data_file)
                fn_id = body.get('fn_id')
                note = body['note']
                for fn in data['fns']:
                    if fn.get('id') == fn_id:
                        fn.setdefault('notes', []).append(note)
                        break
                save_data(data_file, data)
                self._send_json({'ok': True})

            elif self.path == '/trace':
                data = load_data(data_file)
                highlighted = parse_trace(body['text'], data['fns'])
                self._send_json({'highlighted_fns': highlighted})

            else:
                self.send_error(404)

        def _serve_file(self, path, content_type):
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            with open(path, 'rb') as f:
                self.wfile.write(f.read())

        def _send_json(self, data, no_cache=False):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(body))
            if no_cache:
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    return Handler


def run(data_file, flow_file, port, skill_dir, open_browser=True):
    handler = make_handler(data_file, flow_file, Path(skill_dir))
    httpd = http.server.HTTPServer(('localhost', port), handler)
    if open_browser:
        webbrowser.open(f'http://localhost:{port}')
    httpd.serve_forever()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: server.py <data.json> [flow.json] [port]', file=sys.stderr)
        sys.exit(1)
    data_file = sys.argv[1]
    # Accept: server.py data.json flow.json port  OR  server.py data.json port
    flow_file = None
    port = 8742
    if len(sys.argv) >= 3:
        if sys.argv[2].endswith('.json'):
            flow_file = sys.argv[2]
            port = int(sys.argv[3]) if len(sys.argv) >= 4 else 8742
        else:
            port = int(sys.argv[2])
    skill_dir = Path(__file__).parent
    print(f'Starting server at http://localhost:{port}')
    run(data_file, flow_file, port, skill_dir)
