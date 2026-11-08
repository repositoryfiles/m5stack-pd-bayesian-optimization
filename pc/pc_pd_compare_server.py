"""Core2 PD gain comparison: A/B/C repeated three times.

Run in PC Thonny (Local Python 3). Standard library only.
Core2 protocol: GET /gains -> {trial,kp,kd}; POST /result with
trial,kp,kd,rms,success,elapsed_ms,samples.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from pathlib import Path
import csv
import json
import math
import statistics
import threading

HOST = '0.0.0.0'
PORT = 8000
CSV_PATH = Path(__file__).with_name('pd_compare_results.csv')
SUMMARY_PATH = Path(__file__).with_name('pd_compare_summary.csv')
FIELDS = ['datetime', 'trial', 'condition', 'repeat', 'kp', 'kd',
          'rms', 'success', 'elapsed_ms', 'samples']
PLAN = [
    {'condition': condition, 'repeat': repeat, 'kp': kp, 'kd': kd}
    for repeat in range(1, 4)
    for condition, kp, kd in [('A', 40.0, 1.0),
                              ('B', 42.0, 0.95),
                              ('C', 42.0, 0.973)]
]
for i, item in enumerate(PLAN, start=1):
    item['trial'] = i

lock = threading.Lock()

# Resume existing CSV rather than overwriting it after an accidental restart.
if not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0:
    with CSV_PATH.open('w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(FIELDS)

with CSV_PATH.open('r', newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    if reader.fieldnames != FIELDS:
        raise SystemExit('CSV columns do not match this program. Rename the old CSV.')
    results = list(reader)
if len(results) > len(PLAN):
    raise SystemExit('CSV contains too many rows.')
for index, row in enumerate(results):
    expected = PLAN[index]
    if (int(row['trial']) != expected['trial'] or
        row['condition'] != expected['condition'] or
        not math.isclose(float(row['kp']), expected['kp'], abs_tol=1e-6) or
        not math.isclose(float(row['kd']), expected['kd'], abs_tol=1e-6)):
        raise SystemExit('CSV does not match this experiment plan. Rename the old CSV.')


def write_summary():
    with SUMMARY_PATH.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['condition', 'kp', 'kd', 'completed', 'successes',
                         'mean_rms_success_only', 'sample_sd_rms_success_only'])
        for condition, kp, kd in [('A', 40.0, 1.0),
                                  ('B', 42.0, 0.95),
                                  ('C', 42.0, 0.973)]:
            subset = [r for r in results if r['condition'] == condition]
            good = [float(r['rms']) for r in subset
                    if r['success'] == 'True' and r['rms'] not in ('', 'None')]
            mean = statistics.mean(good) if good else ''
            sd = statistics.stdev(good) if len(good) >= 2 else ''
            writer.writerow([condition, kp, kd, len(subset), len(good), mean, sd])
            print(f'{condition}: {len(subset)}/3 trials, {len(good)} successes, '
                  f'mean RMS={mean}, sample SD={sd}')
    print('Summary saved:', SUMMARY_PATH)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, code=200):
        payload = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        with lock:
            if self.path == '/gains':
                if len(results) == len(PLAN):
                    self.send_json({'status': 'finished', 'trials': len(results)}, 409)
                else:
                    item = PLAN[len(results)]
                    self.send_json({'trial': item['trial'],
                                    'kp': item['kp'], 'kd': item['kd']})
            elif self.path == '/status':
                self.send_json({'completed': len(results), 'total': len(PLAN)})
            else:
                self.send_error(404)

    def do_POST(self):
        if self.path != '/result':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                self.send_json({'error': 'invalid body length'}, 400)
                return
            data = json.loads(self.rfile.read(length).decode('utf-8'))
            with lock:
                if len(results) == len(PLAN):
                    self.send_json({'error': 'all trials finished'}, 409)
                    return
                expected = PLAN[len(results)]
                if int(data['trial']) != expected['trial']:
                    self.send_json({'error': 'trial mismatch',
                                    'expected': expected['trial']}, 409)
                    return
                if (not math.isclose(float(data['kp']), expected['kp'], abs_tol=0.0001) or
                    not math.isclose(float(data['kd']), expected['kd'], abs_tol=0.00001)):
                    self.send_json({'error': 'gain mismatch'}, 409)
                    return
                if type(data['success']) is not bool:
                    self.send_json({'error': 'success must be boolean'}, 400)
                    return
                success = data['success']
                rms = data.get('rms')
                if success:
                    if rms is None or not math.isfinite(float(rms)) or float(rms) < 0:
                        self.send_json({'error': 'invalid RMS'}, 400)
                        return
                    rms = float(rms)
                else:
                    rms = None
                elapsed = int(data['elapsed_ms'])
                samples = int(data['samples'])
                if elapsed < 0 or samples < 0:
                    self.send_json({'error': 'invalid timing'}, 400)
                    return
                row = {'datetime': datetime.now().isoformat(timespec='seconds'),
                       'trial': expected['trial'], 'condition': expected['condition'],
                       'repeat': expected['repeat'], 'kp': expected['kp'],
                       'kd': expected['kd'], 'rms': rms if rms is not None else '',
                       'success': str(success), 'elapsed_ms': elapsed,
                       'samples': samples}
                with CSV_PATH.open('a', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=FIELDS)
                    writer.writerow(row)
                results.append({key: str(value) for key, value in row.items()})
                print('Received:', row)
                if len(results) == len(PLAN):
                    print('\n=== All 9 trials complete ===')
                    write_summary()
                    self.send_json({'status': 'finished', 'completed': len(results)})
                else:
                    next_item = PLAN[len(results)]
                    print('Next:', next_item)
                    self.send_json({'status': 'ok', 'next_trial': next_item['trial']})
        except (KeyError, ValueError, TypeError, OverflowError, json.JSONDecodeError) as exc:
            self.send_json({'error': str(exc)}, 400)
        except Exception as exc:
            print('Server error:', repr(exc))
            self.send_json({'error': 'server error'}, 500)


if __name__ == '__main__':
    print('PD comparison server on port', PORT)
    print('Results:', CSV_PATH)
    print('Completed:', len(results), '/ 9')
    if len(results) < len(PLAN):
        print('Next:', PLAN[len(results)])
    else:
        write_summary()
    HTTPServer((HOST, PORT), Handler).serve_forever()
