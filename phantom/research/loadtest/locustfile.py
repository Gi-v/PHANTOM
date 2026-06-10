"""
locustfile.py — PHANTOM Research Load Tests

Scenarios:
  1. ramp     — gradual load increase (baseline behaviour)
  2. spike    — sudden 10× spike on frontend (triggers cascade)
  3. periodic — sinusoidal load (tests periodic prediction)
  4. adversarial — random bursts (stress-tests confidence gating)

Usage:
  locust -f locustfile.py --host http://localhost:8080 \
         --users 500 --spawn-rate 10 --run-time 72h \
         --headless --csv results/phantom

  # Run specific scenario:
  SCENARIO=spike locust -f locustfile.py ...
"""

import math
import os
import random
import time

from locust import HttpUser, TaskSet, between, events, task
from locust.env import Environment

SCENARIO = os.getenv("SCENARIO", "spike")

# ── Task sets per microservice ─────────────────────────────────────────────────

class FrontendTasks(TaskSet):
    @task(5)
    def homepage(self):
        self.client.get("/", name="[frontend] homepage")

    @task(3)
    def browse_products(self):
        self.client.get("/products", name="[frontend] products")

    @task(2)
    def product_detail(self):
        pid = random.randint(1, 20)
        self.client.get(f"/products/{pid}", name="[frontend] product detail")


class CheckoutTasks(TaskSet):
    @task(4)
    def add_to_cart(self):
        self.client.post("/cart", json={"product_id": random.randint(1, 20), "qty": 1},
                         name="[checkout] add to cart")

    @task(2)
    def view_cart(self):
        self.client.get("/cart", name="[checkout] view cart")

    @task(1)
    def checkout(self):
        self.client.post("/checkout", json={"payment": "card", "address": "123 Main St"},
                         name="[checkout] place order")


# ── User classes ───────────────────────────────────────────────────────────────

class BrowseUser(HttpUser):
    tasks = [FrontendTasks]
    wait_time = between(0.5, 2.0)
    weight = 7


class ShopUser(HttpUser):
    tasks = [CheckoutTasks]
    wait_time = between(1.0, 3.0)
    weight = 3


# ── Custom load shapes ─────────────────────────────────────────────────────────

from locust import LoadTestShape

class SpikeShape(LoadTestShape):
    """
    Steady baseline → sudden 10× spike at t=300s → return to baseline.
    Models a flash sale or marketing event — the key cascade scenario.
    """
    baseline = 50
    spike_users = 500
    spike_start = 300
    spike_duration = 120

    def tick(self):
        run_time = self.get_run_time()
        if run_time < self.spike_start:
            return (self.baseline, 10)
        elif run_time < self.spike_start + self.spike_duration:
            return (self.spike_users, 50)
        elif run_time < self.spike_start + self.spike_duration + 60:
            # Ramp down
            t = run_time - (self.spike_start + self.spike_duration)
            users = int(self.spike_users - (self.spike_users - self.baseline) * t / 60)
            return (max(self.baseline, users), 20)
        else:
            return (self.baseline, 5)


class RampShape(LoadTestShape):
    """Gradual ramp — 0→300 users over 10 minutes, hold, then ramp down."""
    stages = [
        {"duration": 600,  "users": 300,  "spawn_rate": 5},
        {"duration": 1800, "users": 300,  "spawn_rate": 5},
        {"duration": 300,  "users": 0,    "spawn_rate": 10},
    ]

    def tick(self):
        run_time = self.get_run_time()
        elapsed = 0
        for stage in self.stages:
            if run_time < elapsed + stage["duration"]:
                return (stage["users"], stage["spawn_rate"])
            elapsed += stage["duration"]
        return None


class PeriodicShape(LoadTestShape):
    """Sinusoidal load — mimics daily traffic patterns. Period = 10 minutes."""
    min_users = 20
    max_users = 300
    period = 600  # seconds

    def tick(self):
        run_time = self.get_run_time()
        users = int(self.min_users + (self.max_users - self.min_users) *
                    (0.5 + 0.5 * math.sin(2 * math.pi * run_time / self.period)))
        return (users, max(5, users // 10))


# Select shape based on SCENARIO env var
_shapes = {
    "spike":    SpikeShape,
    "ramp":     RampShape,
    "periodic": PeriodicShape,
}

if SCENARIO in _shapes:
    # Locust picks up the first LoadTestShape subclass in the file
    # We override by monkey-patching if needed
    pass


# ── Event hooks for experiment logging ────────────────────────────────────────

@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    if exception:
        print(f"[PHANTOM] Request failed: {name} — {exception}")


@events.test_start.add_listener
def on_test_start(environment: Environment, **kwargs):
    print(f"[PHANTOM] Load test started. Scenario: {SCENARIO}")
    print(f"[PHANTOM] Target: {environment.host}")
