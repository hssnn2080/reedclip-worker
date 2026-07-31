from vastai import Worker, WorkerConfig, HandlerConfig, BenchmarkConfig, LogActionConfig
import subprocess
import os

print("Installing required system packages (ffmpeg, aria2)...")
os.system("apt-get update && apt-get install -y ffmpeg aria2")

# Start the local FastAPI server in the background and pipe output to a log file
server_log = open("server.log", "w")
subprocess.Popen(["python3", "server.py"], stdout=server_log, stderr=subprocess.STDOUT)

worker_config = WorkerConfig(
    model_server_url="http://127.0.0.1",
    model_server_port=18000,
    model_log_file="server.log",
    handlers=[
        HandlerConfig(
            route="/generate",
            allow_parallel_requests=False,
            max_queue_time=600.0,
            workload_calculator=lambda payload: 100.0,
            benchmark_config=BenchmarkConfig(
                generator=lambda: {"jobId": "test_benchmark", "audioUrl": "https://www.w3schools.com/html/horse.ogg", "videoUrl": "https://www.w3schools.com/html/mov_bbb.mp4"},
                runs=1,
                concurrency=1,
            ),
        )
    ],
    log_action_config=LogActionConfig(
        on_load=["Application startup complete."],
        on_error=["Traceback", "Exception:", "failed:"],
    ),
)

if __name__ == "__main__":
    Worker(worker_config).run()
