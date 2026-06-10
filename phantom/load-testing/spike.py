#!/usr/bin/env python3
"""
spike.py — trigger a sudden traffic spike for cascade demo
Usage: python spike.py --multiplier 10 --duration 5m --host http://localhost:8080
"""
import argparse, subprocess, sys, time, re

def parse_duration(s: str) -> int:
    m = re.match(r'^(\d+)(s|m|h)$', s)
    if not m: raise ValueError(f"Bad duration: {s}")
    v, u = int(m.group(1)), m.group(2)
    return v * {'s':1,'m':60,'h':3600}[u]

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--multiplier', type=int, default=10)
    p.add_argument('--duration',   default='5m')
    p.add_argument('--host',       default='http://localhost:8080')
    p.add_argument('--base-users', type=int, default=50)
    args = p.parse_args()

    secs = parse_duration(args.duration)
    users = args.base_users * args.multiplier

    print(f"[spike] Injecting {users} users for {args.duration} against {args.host}")
    cmd = [
        'locust', '-f', 'locustfile.py',
        '--host', args.host,
        '--users', str(users),
        '--spawn-rate', str(users // 10),
        '--run-time', args.duration,
        '--headless',
        '--csv', f'results/spike_{users}u',
    ]
    subprocess.run(cmd, check=True)

if __name__ == '__main__':
    main()
