# M5Stack Core2 二輪倒立振子のPDゲインをベイズ最適化する

M5Stack Core2とRoller485 Lite（2台）を使った二輪倒立振子について、実機の角度RMSを評価指標としてPDゲイン（Kp、Kd）を探索する実験です。

## 構成

- Core2: IMU計測、PD制御、モータ駆動、Wi-Fi通信、角度RMS算出
- PC: HTTPサーバー、ベイズ最適化、CSV保存
- 通信: Core2が `GET /gains` でゲインを取得し、`POST /result` で測定結果を送信
- 実験開始: Core2のボタン操作で手動開始（安全上、通信だけでモータを起動しない）

## ファイル

- `pc/pc_pd_bayes_server.py`: ベイズ最適化、10試行
- `pc/pc_pd_compare_server.py`: A/B/C各3試行の比較実験
- `results/pd_bayes_results.csv`: 実測の探索結果（10試行）
- `results/pd_compare_summary.csv`: 実測の比較実験集計（各3試行）
- `requirements.txt`: PC用依存ライブラリ

## 実行環境と設定

PCではPython 3と以下のライブラリを使用します。

```bash
python -m pip install -r requirements.txt
```

Core2側のHTTP接続先を、実際に使用するPCのIPアドレスとポートに設定します。PCサーバーのポートは既定で `8000` です。PCとCore2は同じLANに接続します。

## 1. ベイズ最適化

```bash
python pc/pc_pd_bayes_server.py
```

- 探索範囲: Kp=38–42、Kd=0.95–1.05
- 初回: Kp=40、Kd=1
- 試行回数: 10回
- 評価: 10秒間の制御のうち、開始2秒後からの8秒間の角度RMS
- 転倒した試行: 目的関数にペナルティ20を適用

PCサーバーを起動したまま、Core2で各試行を手動実施します。各試行後、Core2を再起動して次のゲインを取得します。既存の `pd_bayes_results.csv` がある場合、サーバーは誤上書きを避けるため起動を停止します。新しい実験を始める前にCSVを退避してください。

## 2. 比較実験

最適化サーバーを停止し、次を起動します。

```bash
python pc/pc_pd_compare_server.py
```

A (40, 1.000) → B (42, 0.950) → C (42, 0.973) の順に3巡し、合計9回を測定します。PC側が各条件の平均RMSと標本標準偏差を計算します。既存の比較実験CSVがあれば途中から再開できます。

## 実測結果

### ベイズ最適化（10試行）

10回すべて倒立に成功しました。最小の実測RMSは試行10の **0.19195824°**（Kp=41.9995、Kd=0.97304）でした。

### 比較実験（各3試行）

| 条件 | Kp | Kd | 平均RMS（°） | 標本標準偏差（°） | 成功 |
|---|---:|---:|---:|---:|---:|
| A | 40.0 | 1.000 | 0.6121 | 0.2248 | 3/3 |
| B | 42.0 | 0.950 | 0.6104 | 0.4650 | 3/3 |
| C | 42.0 | 0.973 | 0.3179 | 0.2056 | 3/3 |

Cの平均RMSはAより約48%小さくなりました。ただし各条件3回であり、統計的に有意な改善や大域的最適解の発見を示すものではありません。実験開始時の姿勢、ジグ退避、電池状態なども結果に影響し得ます。

## 安全上の注意

二輪倒立振子は転倒・急発進する可能性があります。十分な空間を確保し、モータの出力制限・転倒検知・停止処理を確認してから実験してください。無人運転は避けてください。

## Core2 firmware (`core2/main.py`)

The provided `core2/main.py` is the experiment version with Wi-Fi credentials and the PC server address replaced by placeholders. Set `SSID`, `PASSWORD`, and `SERVER` for your network before running. The firmware requires UIFlow2 MicroPython with `M5`, `requests`, and `Roller485Unit`. Save it as `main.py` on Core2 for startup execution.

**Screen colors:** cyan = initialization / Wi-Fi, yellow = calibration button wait and calibration, blue = launch button wait, green = trial / uploaded, purple = uploading, red = error. Place the robot on a horizontal fixture, press A to calibrate, then move to the inclined starting fixture and press A to begin. The motors stop before the result is uploaded. Restart Core2 for each new trial.

**Safety:** Run in a clear area with a way to cut power. The control loop does not check button A for emergency stop during the 10-second trial. Verify motor direction and fixture stability before testing.

The angle RMS is computed relative to the calibration offset; it is not a direct measurement of absolute vertical alignment or wheel travel.
