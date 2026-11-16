import subprocess

print("Dang khoi dong he thong chay ngam...")

# Thu bat Redis bang Docker (neu da tao)
subprocess.run(["docker", "start", "influencermatch_redis"], capture_output=True)

commands = [
    ["venv\\Scripts\\uvicorn.exe", "main:app", "--port", "8000", "--reload"],
    [
        "venv\\Scripts\\celery.exe",
        "-A",
        "app.workers.celery_app",
        "worker",
        "--loglevel=info",
        "--pool=threads",
        "--concurrency=1",
    ],
]

pids = []
log_file = open("server.log", "w")
for cmd in commands:
    p = subprocess.Popen(
        cmd,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pids.append(str(p.pid))

with open("service_pids.txt", "w") as f:
    f.write(",".join(pids))

print("THANH CONG! He thong da chay ngam hoan toan.")
