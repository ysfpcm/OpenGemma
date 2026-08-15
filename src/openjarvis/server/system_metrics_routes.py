from fastapi import APIRouter, Request
from pydantic import BaseModel
import psutil
import time
import subprocess
from typing import Optional
import platform

class SystemMetrics(BaseModel):
    ram_gb_total: float
    ram_gb_used: float
    cpu_percent: float
    api_routes_count: int
    latency_ms: Optional[float] = None
    
router = APIRouter(prefix="/v1/system", tags=["system"])

@router.get("/metrics", response_model=SystemMetrics)
def get_metrics(request: Request) -> SystemMetrics:
    # RAM
    vm = psutil.virtual_memory()
    ram_gb_total = round(vm.total / (1024 ** 3), 1)
    ram_gb_used = round(vm.used / (1024 ** 3), 1)
    
    # CPU
    cpu_percent = psutil.cpu_percent(interval=None)  # Use None interval to avoid blocking
    
    # API Routes count
    app = request.app
    # Count unique routes roughly
    api_routes_count = len(app.routes) if hasattr(app, "routes") else 0
    
    # Latency: ping 8.8.8.8
    latency_ms = None
    try:
        sys_os = platform.system().lower()
        if sys_os == "windows":
            cmd = ["ping", "-n", "1", "-w", "1000", "8.8.8.8"]
        else:
            cmd = ["ping", "-c", "1", "-W", "1", "8.8.8.8"]
            
        start = time.time()
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
        if result.returncode == 0:
            latency_ms = round((time.time() - start) * 1000)
    except Exception:
        pass

    return SystemMetrics(
        ram_gb_total=ram_gb_total,
        ram_gb_used=ram_gb_used,
        cpu_percent=cpu_percent,
        api_routes_count=api_routes_count,
        latency_ms=latency_ms
    )
