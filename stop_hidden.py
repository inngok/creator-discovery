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
    print("Thanh cong! Da tieu diet toan bo he thong chay ngam.")
else:
    print("Khong tim thay file luu tru PID (service_pids.txt). Co the he thong dang khong chay.")
