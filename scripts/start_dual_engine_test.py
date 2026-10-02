"""双引擎集成测试启动脚本

一键启动llama.cpp和vLLM服务，用于运行双引擎一致性集成测试。
支持GPU和CPU两种模式。

使用方式：
    # GPU模式（默认）
    python scripts/start_dual_engine_test.py --llama-model ./models/qwen2.5-7b-q4_k_m.gguf --vllm-model Qwen/Qwen2.5-1.5B-Instruct

    # CPU模式（无需GPU）
    python scripts/start_dual_engine_test.py --device cpu --llama-model ./models/qwen2.5-7b-q4_k_m.gguf --vllm-model Qwen/Qwen2.5-1.5B-Instruct

    # 仅启动vLLM（llama.cpp需单独启动）
    python scripts/start_dual_engine_test.py --vllm-only --vllm-model Qwen/Qwen2.5-1.5B-Instruct

启动后运行集成测试：
    pytest tests/integration/test_dual_engine_consistency.py -v
"""

import argparse
import os
import signal
import subprocess
import sys
import time

import httpx


def wait_for_service(url: str, timeout: float = 120.0, interval: float = 2.0) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            with httpx.Client() as client:
                resp = client.get(f"{url}/health", timeout=3.0)
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def start_llama_cpp(model: str, host: str = "0.0.0.0", port: int = 8001,
                    n_ctx: int = 4096, n_gpu_layers: int = -1,
                    extra_args: list[str] | None = None) -> subprocess.Popen:
    cmd = [
        sys.executable, "-m", "llama_cpp.server",
        "--model", model,
        "--host", host,
        "--port", str(port),
        "--n_ctx", str(n_ctx),
        "--n_gpu_layers", str(n_gpu_layers),
    ]
    if extra_args:
        cmd.extend(extra_args)
    print(f"[llama.cpp] Starting: {' '.join(cmd)}")
    return subprocess.Popen(cmd)


def start_vllm(model: str, host: str = "0.0.0.0", port: int = 8002,
               device: str = "cuda", gpu_memory_utilization: float = 0.5,
               cpu_kv_cache_capacity: int | None = None,
               extra_args: list[str] | None = None) -> subprocess.Popen:
    script_path = os.path.join(os.path.dirname(__file__), "vllm_server.py")
    cmd = [
        sys.executable, script_path,
        "--model", model,
        "--host", host,
        "--port", str(port),
        "--device", device,
    ]
    if device == "cuda":
        cmd.extend(["--gpu-memory-utilization", str(gpu_memory_utilization)])
    elif device == "cpu" and cpu_kv_cache_capacity is not None:
        cmd.extend(["--cpu-kv-cache-capacity", str(cpu_kv_cache_capacity)])
    if extra_args:
        cmd.extend(extra_args)
    print(f"[vLLM] Starting: {' '.join(cmd)}")
    return subprocess.Popen(cmd)


def main():
    parser = argparse.ArgumentParser(description="Start dual-engine services for integration testing")
    parser.add_argument("--llama-model", default="", help="Path to GGUF model for llama.cpp")
    parser.add_argument("--vllm-model", default="", help="Model name/path for vLLM")
    parser.add_argument("--llama-host", default="0.0.0.0")
    parser.add_argument("--llama-port", type=int, default=8001)
    parser.add_argument("--vllm-host", default="0.0.0.0")
    parser.add_argument("--vllm-port", type=int, default=8002)
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                        help="Device for vLLM (cuda or cpu)")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.5)
    parser.add_argument("--cpu-kv-cache-capacity", type=int, default=None)
    parser.add_argument("--llama-only", action="store_true", help="Only start llama.cpp")
    parser.add_argument("--vllm-only", action="store_true", help="Only start vLLM")
    parser.add_argument("--wait-timeout", type=float, default=120.0,
                        help="Max seconds to wait for each service to become healthy")
    parser.add_argument("--n-ctx", type=int, default=4096, help="Context length for llama.cpp")
    parser.add_argument("--n-gpu-layers", type=int, default=-1, help="GPU layers for llama.cpp (-1=all)")
    args = parser.parse_args()

    processes = []

    def cleanup(signum=None, frame=None):
        print("\nShutting down services...")
        for p in processes:
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    if not args.llama_only and not args.vllm_only and not args.llama_model and not args.vllm_model:
        parser.error("At least one of --llama-model or --vllm-model is required")

    if not args.vllm_only and args.llama_model:
        p = start_llama_cpp(
            model=args.llama_model,
            host=args.llama_host,
            port=args.llama_port,
            n_ctx=args.n_ctx,
            n_gpu_layers=args.n_gpu_layers,
        )
        processes.append(("llama.cpp", p))

    if not args.llama_only and args.vllm_model:
        p = start_vllm(
            model=args.vllm_model,
            host=args.vllm_host,
            port=args.vllm_port,
            device=args.device,
            gpu_memory_utilization=args.gpu_memory_utilization,
            cpu_kv_cache_capacity=args.cpu_kv_cache_capacity,
        )
        processes.append(("vLLM", p))

    print(f"\nWaiting for services to become healthy (timeout={args.wait_timeout}s)...")

    all_healthy = True
    for name, p in processes:
        if name == "llama.cpp":
            url = f"http://{args.llama_host}:{args.llama_port}"
        else:
            url = f"http://{args.vllm_host}:{args.vllm_port}"

        if wait_for_service(url, timeout=args.wait_timeout):
            print(f"  [{name}] Healthy at {url}")
        else:
            print(f"  [{name}] FAILED to start at {url}")
            all_healthy = False

    if all_healthy:
        print("\nAll services are healthy. Press Ctrl+C to stop.")
        print(f"  llama.cpp: http://{args.llama_host}:{args.llama_port}")
        print(f"  vLLM:      http://{args.vllm_host}:{args.vllm_port}")
        print("\nRun integration tests:")
        print("  pytest tests/integration/test_dual_engine_consistency.py -v")
        try:
            for _, p in processes:
                p.wait()
        except KeyboardInterrupt:
            cleanup()
    else:
        print("\nSome services failed to start. Cleaning up...")
        cleanup()


if __name__ == "__main__":
    main()
