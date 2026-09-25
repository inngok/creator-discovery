import subprocess
import os

print("Dang khoi dong he thong chay ngam...")

# Thu bat Redis bang Docker (neu da tao)
subprocess.run(["docker", "start", "influencermatch_redis"], capture_output=True)

commands = [
    ["app\\ai_development\\.venv\\Scripts\\uvicorn.exe", "main:app", "--port", "8001"],
    ["app\\ai_development\\.venv\\Scripts\\celery.exe", "-A", "app.workers.celery_app", "worker", "--loglevel=info", "--pool=threads"],
    ["app\\ai_development\\.venv\\Scripts\\python.exe", "-m", "app.scripts.discovery_bot"]
]

pids = []
for cmd in commands:
    p = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pids.append(str(p.pid))

with open("service_pids.txt", "w") as f:
    f.write(",".join(pids))

print("THANH CONG! He thong da chay ngam hoan toan.")
print("Khong co cua so nao bi pop-up lam phien ban nua.")
print("-------------------------------------------------")
print("De TAT he thong nay, hay chay file: stop_all.bat")
