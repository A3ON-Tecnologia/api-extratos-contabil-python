
import requests

try:
    r = requests.get("http://localhost:8888/monitor/stats")
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
except Exception as e:
    print(f"Error: {e}")
