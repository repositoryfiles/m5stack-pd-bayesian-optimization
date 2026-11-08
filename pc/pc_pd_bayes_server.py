"""PC-side HTTP server for sequential Bayesian optimization of PD gains.
Install on PC: py -m pip install numpy scipy scikit-learn
Run: py pc_pd_bayes_server.py
Core2: GET /gains and POST /result, with physical button start.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from pathlib import Path
import csv
import json
import math
import threading

import numpy as np
from scipy.stats import norm
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

HOST = "0.0.0.0"
PORT = 8000
BOUNDS = np.array([[38.0, 42.0], [0.95, 1.05]])
MAX_TRIALS = 10
FAIL_PENALTY = 20.0
CSV_PATH = Path(__file__).with_name("pd_bayes_results.csv")
FIELDS = ["datetime", "trial", "kp", "kd", "rms", "success", "elapsed_ms", "samples", "objective"]
lock = threading.Lock()

# Restart is intentionally a fresh experiment. Archive the previous CSV manually first.
if CSV_PATH.exists() and CSV_PATH.stat().st_size > 0:
    raise SystemExit(f"Existing {CSV_PATH.name} found. Rename/archive it before starting a NEW optimization run.")
with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
    csv.writer(f).writerow(FIELDS)

observations = []
pending = {"trial": 1, "kp": 40.0, "kd": 1.0}

def propose_next():
    """Choose a new point via expected improvement on a normalized 2D domain."""
    x = np.array([[ (r["kp"] - 38.0) / 4.0, (r["kd"] - 0.95) / 0.10] for r in observations])
    y = np.array([r["objective"] for r in observations])
    rng = np.random.default_rng(20261108 + len(observations))
    candidates = rng.uniform(0, 1, size=(2500, 2))
    # Include corners/center to improve exploration coverage.
    candidates = np.vstack((candidates, [[0,0],[0,1],[1,0],[1,1],[0.5,0.5]]))
    distances = np.linalg.norm(candidates[:, None, :] - x[None, :, :], axis=2)
    candidates = candidates[np.min(distances, axis=1) > 0.025]
    if len(observations) < 3:
        # Space-filling initialization after the center.
        choice = candidates[np.argmax(np.min(np.linalg.norm(candidates[:,None,:]-x[None,:,:],axis=2),axis=1))]
    else:
        kernel = ConstantKernel(1.0, (0.01, 100.0)) * Matern(length_scale=[0.4,0.4], length_scale_bounds=(0.05, 5.0), nu=2.5) + WhiteKernel(noise_level=0.05, noise_level_bounds=(1e-4, 10.0))
        gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=2, random_state=0)
        gp.fit(x, y)
        mu, sigma = gp.predict(candidates, return_std=True)
        best = np.min(y)
        xi = 0.03
        improvement = best - mu - xi
        z = improvement / np.maximum(sigma, 1e-9)
        ei = improvement * norm.cdf(z) + sigma * norm.pdf(z)
        choice = candidates[np.argmax(ei)]
    return {"kp": round(38.0 + 4.0 * float(choice[0]), 4), "kd": round(0.95 + 0.10 * float(choice[1]), 5)}

class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, code=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        with lock:
            if self.path == "/gains":
                if pending is None:
                    self.send_json({"status": "finished", "trials": len(observations)}, 409)
                else:
                    self.send_json(pending)
            elif self.path == "/status":
                self.send_json({"completed": len(observations), "max_trials": MAX_TRIALS, "pending": pending})
            else:
                self.send_error(404)

    def do_POST(self):
        global pending
        if self.path != "/result":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                self.send_json({"error": "invalid body length"}, 400)
                return
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            with lock:
                if pending is None:
                    self.send_json({"error": "optimization finished"}, 409)
                    return
                if int(data["trial"]) != pending["trial"]:
                    self.send_json({"error": "trial mismatch", "expected": pending["trial"]}, 409)
                    return
                if abs(float(data["kp"]) - pending["kp"]) > 1e-3 or abs(float(data["kd"]) - pending["kd"]) > 1e-4:
                    self.send_json({"error": "gain mismatch"}, 409)
                    return
                success = data.get("success") is True
                rms = data.get("rms")
                if success and (not isinstance(rms, (int, float)) or not math.isfinite(rms) or rms < 0):
                    self.send_json({"error": "invalid RMS"}, 400)
                    return
                objective = float(rms) if success else FAIL_PENALTY
                row = {"datetime": datetime.now().isoformat(timespec="seconds"), "trial": pending["trial"], "kp": pending["kp"], "kd": pending["kd"], "rms": rms if success else "", "success": success, "elapsed_ms": data.get("elapsed_ms", ""), "samples": data.get("samples", ""), "objective": objective}
                # Persist first; if optimizer fails, result is still recorded.
                with CSV_PATH.open("a", newline="", encoding="utf-8-sig") as f:
                    csv.DictWriter(f, fieldnames=FIELDS).writerow(row)
                observations.append(row)
                print(f"Trial {row['trial']}/{MAX_TRIALS}: Kp={row['kp']} Kd={row['kd']} RMS={row['rms']} objective={objective}", flush=True)
                if len(observations) >= MAX_TRIALS:
                    pending = None
                    best = min(observations, key=lambda r: r["objective"])
                    print("FINISHED. Best observed:", best, flush=True)
                else:
                    try:
                        params = propose_next()
                    except Exception as exc:
                        print("Optimizer error:", repr(exc), flush=True)
                        pending = None
                        self.send_json({"status": "saved_but_optimizer_error", "error": str(exc)}, 500)
                        return
                    pending = {"trial": len(observations) + 1, **params}
                    print("Next gains:", pending, flush=True)
                self.send_json({"status": "ok", "next_trial": None if pending is None else pending["trial"]})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)

if __name__ == "__main__":
    print("Bayesian PD server started on port", PORT)
    print("Results:", CSV_PATH)
    print("Trial 1:", pending)
    print("Start each trial manually on Core2. Restart Core2 after each completed trial.")
    HTTPServer((HOST, PORT), Handler).serve_forever()
