import subprocess
import os

print("Dang tien hanh tat cac tien trinh chay ngam...")

if os.path.exists("service_pids.txt"):
    with open("service_pids.txt", "r") as f:
        pids = f.read().strip().split(",")
    
    for pid in pids:
        if pid:
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
    os.remove("service_pids.txt")
    print("Thanh cong! Da tieu diet cac process co trong service_pids.txt.")
else:
    print("Khong tim thay file luu tru PID (service_pids.txt). Co the he thong dang khong chay.")

print("Dang quet va don dep cac zombie process cua Uvicorn va Celery...")
# Kill by image name
subprocess.run(["taskkill", "/F", "/IM", "uvicorn.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(["taskkill", "/F", "/IM", "celery.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Kill any process holding port 8001
try:
    netstat_out = subprocess.check_output('netstat -aon | find ":8001" | find "LISTENING"', shell=True, text=True)
    for line in netstat_out.strip().split("\n"):
        if line:
            parts = line.split()
            if len(parts) >= 5:
                pid = parts[-1]
                subprocess.run(["taskkill", "/F", "/PID", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"Da xoa zombie process o cong 8001 (PID: {pid})")
except subprocess.CalledProcessError:
    pass # Khong tim thay process nao

print("Da don dep hoan toan!")
