import os

from locust import HttpUser, between, task


class RouterUser(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self):
        api_key = os.environ.get("LOCUST_ROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("Set LOCUST_ROUTER_API_KEY before starting Locust.")
        self.headers = {"X-API-Key": api_key}

    @task
    def chat_completion(self):
        self.client.post(
            "/v1/chat/completions",
            headers=self.headers,
            json={"messages": [{"role": "user", "content": "Return a concise mock response."}]},
            name="/v1/chat/completions",
        )
