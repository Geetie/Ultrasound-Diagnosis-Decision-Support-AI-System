"""本地LLM测试服务启动脚本

一键启动llama.cpp(本地)和vLLM(Docker)服务，用于Phase0A集成测试。

架构说明：
    - llama.cpp: 本地Python进程（Windows/Linux均支持，天然支持CPU）
    - vLLM: Docker容器（vLLM原生不支持Windows，通过Docker运行Linux容器）

使用方式：
    # 启动双引擎（默认：llama.cpp本地 + vLLM Docker）
    python scripts/start_local_test_services.py

    # 仅启动llama.cpp
    python scripts/start_local_test_services.py --llama-only

    # 仅启动vLLM（Docker模式）
    python scripts/start_local_test_services.py --vllm-only

    # vLLM使用原生模式（仅Linux）
    python scripts/start_local_test_services.py --vllm-mode native

    # 指定模型路径
    python scripts/start_local_test_services.py --llama-model ./models/my-model.gguf

启动后运行测试：
    set LLM_TEST_MODE=local
    pytest tests/integration/test_phase0a_consistency.py -v -m phase0a
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(PROJECT_ROOT, "models")
DEFAULT_GGUF = os.path.join(MODELS_DIR, "qwen2.5-1.5b-instruct-q4_k_m.gguf")
DEFAULT_HF = os.path.join(MODELS_DIR, "Qwen2.5-1.5B-Instruct")
VLLM_CONTAINER_NAME = "ultrasound-vllm-test"


def find_gguf_model() -> str:
    if os.path.exists(DEFAULT_GGUF):
        return DEFAULT_GGUF
    if os.path.exists(MODELS_DIR):
        for f in os.listdir(MODELS_DIR):
            if f.endswith(".gguf"):
                return os.path.join(MODELS_DIR, f)
    return ""


def find_hf_model() -> str:
    if os.path.exists(os.path.join(DEFAULT_HF, "config.json")):
        return DEFAULT_HF
    if os.path.exists(MODELS_DIR):
        for d in os.listdir(MODELS_DIR):
            config_path = os.path.join(MODELS_DIR, d, "config.json")
            if os.path.exists(config_path):
                return os.path.join(MODELS_DIR, d)
    return ""


def wait_for_service(url: str, timeout: float = 300.0, interval: float = 3.0) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{url}/health")
                if resp.status_code == 200:
                    return True
        except Exception:
            logger.warning("wait_for_service: 未预期异常，按原逻辑降级", exc_info=True)
            pass
        elapsed = time.monotonic() - start
        if int(elapsed) % 15 == 0 and elapsed > 5:
            print(f"    ... still waiting ({elapsed:.0f}s elapsed)")
        time.sleep(interval)
    return False


def start_llama_cpp(args) -> subprocess.Popen | None:
    llama_model = args.llama_model or find_gguf_model()
    if not llama_model:
        print("ERROR: No GGUF model found. Specify --llama-model or place a .gguf file in models/")
        return None

    base_url = f"http://{args.llama_host}:{args.llama_port}"
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{base_url}/health")
            if resp.status_code == 200:
                print(f"[llama.cpp] Already running at {base_url}")
                return None
    except Exception:
        logger.warning("start_llama_cpp: 未预期异常，按原逻辑降级", exc_info=True)
        pass

    cmd = [
        sys.executable, os.path.join(PROJECT_ROOT, "scripts", "llama_cpp_server.py"),
        "--host", args.llama_host,
        "--port", str(args.llama_port),
        "--n_ctx", str(args.n_ctx),
    ]
    print(f"\n[llama.cpp] Starting: {' '.join(cmd)}")
    print(f"[llama.cpp] Model: {llama_model}")

    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    p = subprocess.Popen(cmd, **popen_kwargs)
    return p


def start_vllm_docker(args) -> bool:
    hf_model_path = args.vllm_model or find_hf_model()
    if not hf_model_path:
        print("ERROR: No HuggingFace model found. Specify --vllm-model or place model in models/")
        return False

    models_dir = os.path.dirname(hf_model_path)
    model_name = os.path.basename(hf_model_path)
    base_url = f"http://{args.vllm_host}:{args.vllm_port}"

    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{base_url}/health")
            if resp.status_code == 200:
                print(f"[vLLM] Already running at {base_url}")
                return True
    except Exception:
        logger.warning("start_vllm_docker: 未预期异常，按原逻辑降级", exc_info=True)
        pass

    subprocess.run(
        ["docker", "rm", "-f", VLLM_CONTAINER_NAME],
        capture_output=True, timeout=10,
    )

    cmd = [
        "docker", "run", "-d",
        "--name", VLLM_CONTAINER_NAME,
        "-p", f"{args.vllm_port}:8002",
        "-v", f"{models_dir}:/models:ro",
        "vllm/vllm-openai:latest",
        "--model", f"/models/{model_name}",
        "--served-model-name", "Qwen2.5-1.5B-Instruct",
        "--host", "0.0.0.0",
        "--port", "8002",
        "--max-model-len", "2048",
        "--dtype", "auto",
    ]
    print(f"\n[vLLM] Starting Docker container: {' '.join(cmd)}")
    print(f"[vLLM] Model: {model_name}, Port: {args.vllm_port}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"[vLLM] Docker start FAILED: {result.stderr}")
        return False

    container_id = result.stdout.strip()[:12]
    print(f"[vLLM] Container started: {container_id}")
    return True


def start_vllm_native(args) -> subprocess.Popen | None:
    if sys.platform == "win32":
        print("ERROR: vLLM native mode not supported on Windows. Use Docker mode (default).")
        return None

    hf_model = args.vllm_model or find_hf_model()
    if not hf_model:
        print("ERROR: No HuggingFace model found.")
        return None

    script_path = os.path.join(PROJECT_ROOT, "scripts", "vllm_server.py")
    cmd = [
        sys.executable, script_path,
        "--model", hf_model,
        "--host", args.vllm_host,
        "--port", str(args.vllm_port),
        "--device", args.device,
    ]
    print(f"\n[vLLM] Starting native: {' '.join(cmd)}")

    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    p = subprocess.Popen(cmd, **popen_kwargs)
    return p


def main():
    parser = argparse.ArgumentParser(
        description="Start local LLM services for Phase0A integration testing"
    )
    parser.add_argument("--llama-model", default="", help="Path to GGUF model (auto-detect if omitted)")
    parser.add_argument("--vllm-model", default="", help="Path to HuggingFace model dir (auto-detect if omitted)")
    parser.add_argument("--llama-host", default="localhost")
    parser.add_argument("--llama-port", type=int, default=8001)
    parser.add_argument("--vllm-host", default="localhost")
    parser.add_argument("--vllm-port", type=int, default=8002)
    parser.add_argument("--device", default="cpu", choices=["cuda", "cpu"],
                        help="Device for vLLM native mode (default: cpu)")
    parser.add_argument("--vllm-mode", default="docker", choices=["docker", "native"],
                        help="vLLM deployment mode: docker (default) or native (Linux only)")
    parser.add_argument("--llama-only", action="store_true", help="Only start llama.cpp")
    parser.add_argument("--vllm-only", action="store_true", help="Only start vLLM")
    parser.add_argument("--wait-timeout", type=float, default=300.0,
                        help="Max seconds to wait for each service")
    parser.add_argument("--n-ctx", type=int, default=4096, help="Context length for llama.cpp")
    args = parser.parse_args()

    processes = []
    vllm_docker = False

    def cleanup(signum=None, frame=None):
        print("\n\nShutting down services...")
        for name, p in processes:
            try:
                print(f"  Stopping {name} (PID={p.pid})...")
                p.terminate()
                p.wait(timeout=15)
            except Exception:
                logger.warning("main.cleanup: 未预期异常，按原逻辑降级", exc_info=True)
                p.kill()
        if vllm_docker:
            print(f"  Removing Docker container {VLLM_CONTAINER_NAME}...")
            subprocess.run(["docker", "rm", "-f", VLLM_CONTAINER_NAME], capture_output=True, timeout=10)
        print("All services stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # Start llama.cpp
    if not args.vllm_only:
        p = start_llama_cpp(args)
        if p is not None:
            processes.append(("llama.cpp", p))

    # Start vLLM
    if not args.llama_only:
        if args.vllm_mode == "docker":
            if start_vllm_docker(args):
                vllm_docker = True
        else:
            p = start_vllm_native(args)
            if p is not None:
                processes.append(("vLLM", p))

    # Wait for health checks
    print(f"\nWaiting for services to become healthy (timeout={args.wait_timeout}s)...")

    all_healthy = True
    if not args.vllm_only:
        url = f"http://{args.llama_host}:{args.llama_port}"
        print(f"  Waiting for llama.cpp at {url}...")
        if wait_for_service(url, timeout=args.wait_timeout):
            print(f"  [llama.cpp] Healthy at {url}")
        else:
            print(f"  [llama.cpp] FAILED to start at {url}")
            all_healthy = False

    if not args.llama_only:
        url = f"http://{args.vllm_host}:{args.vllm_port}"
        print(f"  Waiting for vLLM at {url}...")
        if wait_for_service(url, timeout=args.wait_timeout):
            print(f"  [vLLM] Healthy at {url}")
        else:
            print(f"  [vLLM] FAILED to start at {url}")
            all_healthy = False

    if all_healthy:
        print("\n" + "=" * 60)
        print("ALL SERVICES ARE HEALTHY")
        print("=" * 60)
        if not args.vllm_only:
            print(f"  llama.cpp: http://{args.llama_host}:{args.llama_port} (local process)")
        if not args.llama_only:
            print(f"  vLLM:      http://{args.vllm_host}:{args.vllm_port} (Docker container)")
        print()
        print("Run Phase0A integration tests:")
        print()
        if sys.platform == "win32":
            print("  set LLM_TEST_MODE=local")
            print("  pytest tests/integration/test_phase0a_consistency.py -v -m phase0a")
        else:
            print("  LLM_TEST_MODE=local pytest tests/integration/test_phase0a_consistency.py -v -m phase0a")
        print()
        print("Press Ctrl+C to stop all services.")
        print("=" * 60)

        try:
            for _, p in processes:
                p.wait()
        except KeyboardInterrupt:
            logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
            cleanup()
    else:
        print("\nSome services failed to start. Cleaning up...")
        cleanup()


if __name__ == "__main__":
    main()
