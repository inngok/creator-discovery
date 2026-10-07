import subprocess
import os

print("Dang khoi dong he thong chay ngam...")

# Thu bat Redis bang Docker (neu da tao)
subprocess.run(["docker", "start", "influencermatch_redis"], capture_output=True)

commands = [
    ["venv\\Scripts\\uvicorn.exe", "main:app", "--port", "8001"],
    ["venv\\Scripts\\celery.exe", "-A", "app.workers.celery_app", "worker", "--loglevel=info", "--pool=threads", "--concurrency=1"],
    ["venv\\Scripts\\python.exe", "-m", "app.scripts.discovery_bot"]
]

pids = []
log_file = open("server.log", "w")
for cmd in commands:
    p = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW, stdout=log_file, stderr=log_file)
    pids.append(str(p.pid))

with open("service_pids.txt", "w") as f:
    f.write(",".join(pids))

print("THANH CONG! He thong da chay ngam hoan toan.")
