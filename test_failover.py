# test_failover.py
import requests
import json

URL = "http://localhost:8000/v1/chat/completions"
HEALTH_URL = "http://localhost:8000/health"

payload = {
    "messages": [
        {"role": "user", "content": "Explain what a rate limit is in one short sentence."}
    ]
}

def run_test():
    print("=" * 60)
    print("🧪 MULTI-PROVIDER PROXY LOAD & FAILOVER TEST")
    print("=" * 60)

    for i in range(1, 6):
        print(f"\n--- Request #{i} ---")
        try:
            res = requests.post(URL, json=payload, timeout=10)
            if res.status_code == 200:
                data = res.json()
                provider = data.get("provider_used", "unknown")
                content = data["choices"][0]["message"]["content"]
                print(f"✅ Success! Provider Used: [{provider}]")
                print(f"   Response Preview: {content[:80]}...")
            else:
                print(f"❌ Error {res.status_code}: {res.text}")
        except Exception as e:
            print(f"❌ Request Failed: {e}")

    print("\n" + "=" * 60)
    print("📊 CHECKING PROVIDER HEALTH STATES:")
    print("=" * 60)
    health_res = requests.get(HEALTH_URL).json()
    print(json.dumps(health_res, indent=2))

if __name__ == "__main__":
    run_test()