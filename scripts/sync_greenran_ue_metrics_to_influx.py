#!/usr/bin/env python3
"""Publica amostras de UE do Data Lake no InfluxDB usado pelo Grafana.

O dashboard legado ``per_UE_stats`` espera uma measurement por UE/campo.
Este sincronizador é deliberadamente somente de observabilidade: não altera
o SQLite, não apaga measurements e só publica campos que existem no lake.
"""

import argparse
import json
import sqlite3
import urllib.request
from pathlib import Path


def _measurement(value):
    return str(value).replace('\\', '\\\\').replace(' ', '\\ ' ).replace(',', '\\,')


def _line(measurement, value, timestamp):
    return f"{_measurement(measurement)} value={float(value)} {int(timestamp) * 1_000_000_000}"


def publish(db_path, influx_url, batch_size=4000):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT timestamp, imsi, cell_id, tx_pdus, throughput_kbps,
               backlog_bytes
        FROM ue_metrics
        WHERE timestamp IS NOT NULL AND imsi IS NOT NULL
        ORDER BY timestamp ASC, imsi ASC
        """
    ).fetchall()

    # Campos disponíveis no Data Lake e equivalentes diretos no dashboard.
    # SINR, PRB e erros físicos não são fabricados quando não existem.
    points = []
    for row in rows:
        ue = int(row['imsi'])
        ts = int(row['timestamp'])
        mappings = {
            f"ue_position_cell_{ue}": row['cell_id'],
            f"ue_{ue}_qosflow.pdcppduvolumedl_filter": row['tx_pdus'],
            f"ue_{ue}_tb.totnbrdl.1.ueid": row['tx_pdus'],
            f"ue_{ue}_tb.totnbrdlinitial": row['tx_pdus'],
            f"ue_{ue}_drb.buffersize.qos.ueid": row['backlog_bytes'],
            f"ue_{ue}_drb.uethpdlpdcpbased.ueid": row['throughput_kbps'],
            f"ue_{ue}_drb.uethpdl.ueid": row['throughput_kbps'],
        }
        for measurement, value in mappings.items():
            if value is not None:
                points.append(_line(measurement, value, ts))

    written = 0
    for start in range(0, len(points), batch_size):
        body = ('\n'.join(points[start:start + batch_size]) + '\n').encode()
        request = urllib.request.Request(
            influx_url.rstrip('/') + '/write?db=influx&precision=ns',
            data=body,
            method='POST',
            headers={'Content-Type': 'text/plain'},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f'InfluxDB respondeu HTTP {response.status}')
        written += len(body.splitlines())

    latest = rows[-1]['timestamp'] if rows else None
    return {'rows': len(rows), 'points': written, 'latest_timestamp': latest}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--influx-url', default='http://127.0.0.1:8086')
    args = parser.parse_args()
    result = publish(args.db, args.influx_url)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
