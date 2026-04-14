"""
Ocean Waves POS — 30-Store Load Test
======================================
Simulates a full production environment:
  • 30 stores, each with its own STAFF + OWNER user
  • Each virtual user picks a random store and only uses that store's data
  • 3 user archetypes: Cashier (billing), Manager (reports) , Analyst (read)

Run:
    locust -f locustfile.py --host=http://127.0.0.1:8000 --users 60 --spawn-rate 10 --run-time 2m --headless
    locust -f locustfile.py --host=http://127.0.0.1:8000                # web UI at localhost:8089
"""

import json, os, random, re
from locust import HttpUser, task, between, events

# ── Load credentials seeded by seed_load_test command ────────────────────────
_CRED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_credentials.json")
try:
    with open(_CRED_FILE) as f:
        _DATA = json.load(f)
    STORES   = _DATA["stores"]       # list of {owner, staff, store_id, store_name}
    PASSWORD = _DATA["password"]
except FileNotFoundError:
    # Fallback to real user if credentials file not found
    STORES   = [{"owner": "admin", "staff": "punith_tanuku", "store_id": 4}]
    PASSWORD = "admin123"

# Collect all in-stock product IDs per store from DB at startup
# (We query once; all stores share the same product catalog structure)
_PRODUCT_CACHE = {}   # store_id -> list of {id, retail}


def _get_csrf(client):
    return client.cookies.get("csrftoken", "")


def _login(client, username, password):
    client.get("/login/", name="[setup] GET /login/")
    csrf = _get_csrf(client)
    resp = client.post(
        "/login/",
        data={"username": username, "password": password},
        headers={"X-CSRFToken": csrf, "Referer": client.base_url + "/login/"},
        allow_redirects=True,
        name="[setup] POST /login/",
    )
    return "dashboard" in resp.url or "dashboard" in resp.text


# ════════════════════════════════════════════════════════════════════════════
#  CASHIER — bills 5 × per manager action; uses billing API
# ════════════════════════════════════════════════════════════════════════════
class CashierUser(HttpUser):
    """A busy cashier at one store: opens billing, saves retail bills, prints."""
    weight    = 4          # most users are cashiers
    wait_time = between(1, 4)

    def on_start(self):
        store_info      = random.choice(STORES)
        self.store_id   = store_info["store_id"]
        self.store_name = store_info["store_name"]
        self.logged_in  = _login(self.client, store_info["staff"], PASSWORD)

        # Fetch billing page once to grab available product IDs for this store
        self._products = []
        if self.logged_in:
            r = self.client.get("/billing/", name="[setup] Load billing")
            # Extract data-id attrs from the page to find real product IDs
            matches = re.findall(r'data-id="(\d+)"[^>]*data-stock="([0-9.]+)"[^>]*data-retail="([0-9.]+)"', r.text)
            self._products = [
                {"id": int(pid), "retail": float(price)}
                for pid, stock, price in matches
                if float(stock) > 0.5
            ]
            if not self._products:
                # Fallback: use product IDs 1–20 range
                self._products = [{"id": i, "retail": random.randint(200, 900)} for i in range(1, 10)]

    @task(5)
    def view_billing_page(self):
        self.client.get("/billing/", name="GET /billing/")

    @task(4)
    def save_retail_bill(self):
        if not self.logged_in or not self._products:
            return
        chosen = random.sample(self._products, k=min(random.randint(1, 3), len(self._products)))
        payload = {
            "bill_type":        "RETAIL",
            "payment_mode":     random.choice(["CASH", "UPI", "CARD"]),
            "gst_rate":         0,
            "discount":         random.choice([0, 0, 10]),
            "customer_name":    random.choice(["", "Test Customer"]),
            "customer_phone":   random.choice(["", "9876543210"]),
            "customer_gst":     "",
            "customer_address": "",
            "items": [
                {
                    "product_id":    p["id"],
                    "quantity":      round(random.uniform(0.5, 3.0), 3),
                    "selling_price": p["retail"],
                }
                for p in chosen
            ],
        }
        csrf = _get_csrf(self.client)
        resp = self.client.post(
            "/billing/save/",
            json=payload,
            headers={"X-CSRFToken": csrf, "Referer": self.host + "/billing/"},
            name="POST /billing/save/ [retail]",
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("bill_id"):
                self.client.get(f"/billing/print/{data['bill_id']}/", name="GET /billing/print/")

    @task(2)
    def view_dashboard(self):
        self.client.get("/dashboard/", name="GET /dashboard/ [cashier]")

    @task(1)
    def view_credits(self):
        self.client.get("/credits/", name="GET /credits/ [cashier]")


# ════════════════════════════════════════════════════════════════════════════
#  OWNER / MANAGER — reviews reports, inventory, ledger
# ════════════════════════════════════════════════════════════════════════════
class OwnerUser(HttpUser):
    """Store owner checking reports, inventory and financials."""
    weight    = 2
    wait_time = between(3, 8)

    def on_start(self):
        store_info     = random.choice(STORES)
        self.store_id  = store_info["store_id"]
        self.logged_in = _login(self.client, store_info["owner"], PASSWORD)

    @task(3)
    def view_dashboard(self):
        self.client.get("/dashboard/", name="GET /dashboard/ [owner]")

    @task(3)
    def view_daily_report(self):
        self.client.get("/reports/daily/", name="GET /reports/daily/")

    @task(2)
    def view_monthly_report(self):
        self.client.get("/reports/monthly/", name="GET /reports/monthly/")

    @task(2)
    def view_inventory(self):
        self.client.get("/inventory/", name="GET /inventory/ [owner]")

    @task(2)
    def view_stock_log(self):
        self.client.get("/inventory/stock-log/", name="GET /stock-log/")

    @task(2)
    def view_credits(self):
        self.client.get("/credits/", name="GET /credits/ [owner]")

    @task(1)
    def view_wholesale_customers(self):
        self.client.get("/wholesale-customers/", name="GET /wholesale-customers/")

    @task(1)
    def view_ledger(self):
        self.client.get("/ledger/", name="GET /ledger/")

    @task(1)
    def view_expenses(self):
        self.client.get("/expenses/", name="GET /expenses/")

    @task(1)
    def view_employees(self):
        self.client.get("/employees/", name="GET /employees/")

    @task(1)
    def view_assets(self):
        self.client.get("/assets/", name="GET /assets/")

    @task(1)
    def view_approval_history(self):
        self.client.get("/reports/approvals/", name="GET /reports/approvals/")


# ════════════════════════════════════════════════════════════════════════════
#  SUPERADMIN — cross-store visibility (uses admin account)
# ════════════════════════════════════════════════════════════════════════════
class SuperAdminUser(HttpUser):
    """Simulates the superadmin browsing across stores."""
    weight    = 1
    wait_time = between(4, 10)

    def on_start(self):
        self.logged_in = _login(self.client, "admin", "admin123")

    @task(3)
    def view_dashboard(self):
        self.client.get("/dashboard/", name="GET /dashboard/ [super]")

    @task(2)
    def view_store_list(self):
        self.client.get("/stores/", name="GET /stores/")

    @task(2)
    def view_users(self):
        self.client.get("/users/", name="GET /users/")

    @task(2)
    def view_daily_report(self):
        store = random.choice(STORES)
        self.client.get(f"/reports/daily/?store_id={store['store_id']}", name="GET /reports/daily/ [super]")

    @task(2)
    def view_inventory_cross_store(self):
        self.client.get("/inventory/", name="GET /inventory/ [super]")

    @task(1)
    def view_ledger_cross_store(self):
        store = random.choice(STORES)
        self.client.get(f"/ledger/?store_id={store['store_id']}", name="GET /ledger/ [super]")

    @task(1)
    def view_area_managers(self):
        self.client.get("/area-managers/", name="GET /area-managers/")

    @task(1)
    def view_approvals(self):
        self.client.get("/reports/approvals/", name="GET /reports/approvals/ [super]")


# ════════════════════════════════════════════════════════════════════════════
#  SUMMARY HOOK
# ════════════════════════════════════════════════════════════════════════════
@events.quitting.add_listener
def on_quit(environment, **kw):
    s = environment.stats.total
    reqs = max(s.num_requests, 1)
    print("\n" + "═" * 65)
    print("  OCEAN WAVES POS — 30-STORE LOAD TEST SUMMARY")
    print("═" * 65)
    print(f"  Stores simulated   : {len(STORES)}")
    print(f"  Total Requests     : {s.num_requests}")
    print(f"  Total Failures     : {s.num_failures}  ({s.num_failures/reqs*100:.1f}%)")
    print(f"  Avg Response Time  : {s.avg_response_time:.0f} ms")
    print(f"  Median Response    : {s.get_response_time_percentile(0.5):.0f} ms")
    print(f"  95th Percentile    : {s.get_response_time_percentile(0.95):.0f} ms")
    print(f"  99th Percentile    : {s.get_response_time_percentile(0.99):.0f} ms")
    print(f"  Peak Throughput    : {s.total_rps:.1f} req/s")
    print("═" * 65)
