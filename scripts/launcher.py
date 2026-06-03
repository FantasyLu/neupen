"""
Neupen Mac 启动器
负责启动 Streamlit 服务并打开浏览器
"""
import os
import sys
import subprocess
import time
import webbrowser
import signal

PORT = 8501
URL = f"http://localhost:{PORT}"


def get_app_data_dir():
    """获取 macOS 应用数据目录"""
    home = os.path.expanduser("~")
    data_dir = os.path.join(home, "Library", "Application Support", "AINovelWriter")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def main():
    # 设置数据目录到 ~/Library/Application Support/AINovelWriter/
    data_dir = get_app_data_dir()
    os.environ["DATA_DIR"] = data_dir
    os.environ["LANCEDB_DIR"] = os.path.join(data_dir, "lancedb")

    # 定位 app.py（冻结环境下在 _MEIPASS 内）
    if getattr(sys, "frozen", False):
        resources = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        resources = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    app_path = os.path.join(resources, "app.py")

    # 启动 Streamlit
    proc = subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", app_path,
        f"--server.port={PORT}",
        "--server.address=localhost",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ])

    # 等待服务启动后打开浏览器
    import urllib.request
    for _ in range(30):
        try:
            urllib.request.urlopen(URL, timeout=1)
            break
        except Exception:
            time.sleep(1)

    webbrowser.open(URL)

    # 等待进程结束（Ctrl-C 或关闭窗口时终止）
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
