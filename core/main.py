"""Seedwake Phase 1 — Minimal thought-stream loop.

Usage: python -m core.main [--config config.yml] [--log data/test.txt]
"""

import argparse
import signal
import sys
import time
from collections import deque
from pathlib import Path

import yaml
from dotenv import load_dotenv

from core.cycle import run_cycle
from core.thought_parser import Thought

# Terminal colors
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_TYPE = {
    "思考": "\033[36m",    # cyan
    "意图": "\033[33m",    # yellow
    "反应": "\033[32m",    # green
}

_log_file = None


def main() -> None:
    global _log_file
    load_dotenv()
    args = _parse_args()
    config = _load_config(args.config)

    if args.log:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        _log_file = open(args.log, "w", encoding="utf-8")

    identity = config["bootstrap"]["identity"]
    model_config = config["models"]["primary"]
    context_window = config["short_term_memory"]["context_window_size"]
    runtime_config = config.get("runtime", {})
    retry_delay = float(runtime_config.get("error_retry_delay_seconds", 1.0))
    max_retry_delay = float(runtime_config.get("max_error_retry_delay_seconds", 10.0))
    current_retry_delay = retry_delay

    thoughts: deque[Thought] = deque(maxlen=context_window * 3)
    cycle_id = 0

    _install_signal_handler()

    _output("Seedwake v0.1 — 心相续引擎启动")
    _output(f"模型: {model_config['name']}  上下文窗口: {context_window} 轮")
    _output("─" * 60)

    while True:
        cycle_id += 1
        try:
            new_thoughts = run_cycle(
                cycle_id, identity, list(thoughts), context_window, model_config,
            )
        except KeyboardInterrupt:
            raise
        except Exception as e:
            _print_error(cycle_id, e, current_retry_delay)
            time.sleep(current_retry_delay)
            current_retry_delay = _next_retry_delay(current_retry_delay, max_retry_delay)
            continue

        for t in new_thoughts:
            thoughts.append(t)

        _print_cycle(cycle_id, new_thoughts)
        current_retry_delay = retry_delay


def _print_cycle(cycle_id: int, thoughts: list[Thought]) -> None:
    # Terminal (with color)
    print(f"\n{C_DIM}── C{cycle_id} ──{C_RESET}")
    for t in thoughts:
        color = C_TYPE.get(t.type, "")
        trigger = f" {C_DIM}(← {t.trigger_ref}){C_RESET}" if t.trigger_ref else ""
        print(f"  {color}[{t.type}]{C_RESET} {t.content}{trigger}")

    # Log file (plain text)
    if _log_file:
        _log_file.write(f"\n── C{cycle_id} ──\n")
        for t in thoughts:
            trigger = f" (← {t.trigger_ref})" if t.trigger_ref else ""
            _log_file.write(f"  [{t.type}] {t.content}{trigger}\n")
        _log_file.flush()


def _output(text: str) -> None:
    """Print to terminal and optionally to log file."""
    print(text)
    if _log_file:
        _log_file.write(text + "\n")
        _log_file.flush()


def _print_error(cycle_id: int, error: Exception, retry_delay: float | None = None) -> None:
    msg = f"── C{cycle_id} ERROR: {error}"
    if retry_delay is not None:
        msg = f"{msg} (retry in {retry_delay:.1f}s)"
    print(f"\n\033[31m{msg}\033[0m", file=sys.stderr)
    if _log_file:
        _log_file.write(f"\n{msg}\n")
        _log_file.flush()


def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        print(f"配置文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seedwake thought-stream engine")
    parser.add_argument("--config", default="config.yml", help="Path to config.yml")
    parser.add_argument("--log", default=None, help="Path to plain-text log file")
    return parser.parse_args()


def _next_retry_delay(current_retry_delay: float, max_retry_delay: float) -> float:
    return min(current_retry_delay * 2, max_retry_delay)


def _install_signal_handler() -> None:
    def handler(sig, frame):
        print(f"\n\n{C_DIM}心相续止息。{C_RESET}")
        if _log_file:
            _log_file.close()
        sys.exit(0)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


if __name__ == "__main__":
    main()
