"""
seed_load_test.py
Creates 30 realistic test stores, 2 staff users per store, 8 products per store,
and 50 historical sales per store so reports/ledger pages have real data to render.

Usage:
    python3 manage.py seed_load_test          # create 30 stores
    python3 manage.py seed_load_test --flush  # wipe test data first then recreate
"""

import random
import decimal
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from django.db import transaction

from pos.models import (
    Store, UserProfile, Product, Sale, SaleItem, StockLog,
    LedgerBook, LedgerEntry, WholesaleCustomer
)

STORE_LOCATIONS = [
    "Hyderabad", "Vijayawada", "Visakhapatnam", "Tirupati", "Guntur",
    "Nellore", "Kurnool", "Rajahmundry", "Kakinada", "Eluru",
    "Ongole", "Anantapur", "Kadapa", "Srikakulam", "Vizianagaram",
    "Bhimavaram", "Tenali", "Narasaraopet", "Chilakaluripeta", "Gudivada",
    "Machilipatnam", "Tadepalligudem", "Palasa", "Amadalavalasa", "Tanuku",
    "Nandyal", "Markapur", "Hindupur", "Dharmavaram", "Adoni",
]

SEAFOOD = [
    ("Rohu Fish",      450, 350, 280),
    ("Catla Fish",     500, 400, 310),
    ("Pomfret",        800, 680, 550),
    ("Prawns (Medium)",600, 480, 380),
    ("Tiger Prawns",   900, 780, 620),
    ("Squid",          350, 280, 210),
    ("Crab",           700, 580, 460),
    ("Mackerel",       300, 240, 180),
    ("Sardine",        200, 160, 120),
    ("Tuna",           750, 620, 500),
    ("Snapper",        650, 530, 420),
    ("King Fish",      850, 720, 580),
]

WS_CUSTOMERS = [
    "Sri Lakshmi Fish Mart", "Ravi Sea Foods", "Balaji Traders",
    "Krishna Fish House", "Sai Seafood Co.", "Durga Enterprises",
    "Venkat Brothers", "Prasad Fish Depot", "Surya Seafoods",
    "Ganesh Fish Supplier",
]

PASSWORD = "testpass123"


class Command(BaseCommand):
    help = "Seed 30 test stores with users, products, sales and ledger data"

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Delete all test data before seeding")
        parser.add_argument("--stores", type=int, default=30, help="Number of test stores to create")

    def handle(self, *args, **options):
        n_stores = options["stores"]

        if options["flush"]:
            self.stdout.write("🗑  Flushing test data...")
            User.objects.filter(username__startswith="staff_").delete()
            User.objects.filter(username__startswith="owner_").delete()
            Store.objects.filter(name__startswith="OW ").delete()
            self.stdout.write("  done.\n")

        self.stdout.write(f"🌱 Seeding {n_stores} stores...\n")
        credentials = []

        with transaction.atomic():
            for i, location in enumerate(STORE_LOCATIONS[:n_stores], start=1):
                store_name = f"OW {location}"
                store, _ = Store.objects.get_or_create(
                    name=store_name,
                    defaults={
                        "address": f"{random.randint(1,99)} Main Road, {location}",
                        "phone":   f"9{random.randint(100000000, 999999999)}",
                        "gstin":   f"37AABCU{random.randint(1000, 9999)}K1Z{random.randint(1,9)}",
                        "is_active": True,
                    }
                )

                # ── Owner user ─────────────────────────────────────
                owner_uname = f"owner_{location.lower().replace(' ','_')}"
                owner, created = User.objects.get_or_create(
                    username=owner_uname,
                    defaults={
                        "first_name": f"Owner",
                        "last_name":  location,
                        "email":      f"{owner_uname}@oceanwaves.test",
                    }
                )
                if created:
                    owner.set_password(PASSWORD)
                    owner.save()
                    UserProfile.objects.create(user=owner, role="OWNER", store=store)

                # ── Staff user ─────────────────────────────────────
                staff_uname = f"staff_{location.lower().replace(' ','_')}"
                staff, created = User.objects.get_or_create(
                    username=staff_uname,
                    defaults={
                        "first_name": "Staff",
                        "last_name":  location,
                        "email":      f"{staff_uname}@oceanwaves.test",
                    }
                )
                if created:
                    staff.set_password(PASSWORD)
                    staff.save()
                    UserProfile.objects.create(user=staff, role="STAFF", store=store)

                credentials.append({
                    "owner": owner_uname,
                    "staff": staff_uname,
                    "store_id": store.id,
                    "store_name": store_name,
                })

                # ── Products (8 per store) ─────────────────────────
                seafood_sample = random.sample(SEAFOOD, 8)
                products = []
                for pname, retail, ws, cost in seafood_sample:
                    p, _ = Product.objects.get_or_create(
                        store=store, name=pname,
                        defaults={
                            "category":       random.choice(["FISH", "PRAWNS", "CRAB"]),
                            "retail_price":   decimal.Decimal(retail),
                            "wholesale_price":decimal.Decimal(ws),
                            "cost_price":     decimal.Decimal(cost),
                            "stock_quantity": decimal.Decimal(random.randint(20, 100)),
                            "low_stock_alert":decimal.Decimal(5),
                        }
                    )
                    products.append(p)

                # ── Wholesale Customers ────────────────────────────
                wc_objects = []
                for wc_name in random.sample(WS_CUSTOMERS, 4):
                    wc, _ = WholesaleCustomer.objects.get_or_create(
                        name=f"{wc_name} ({location})",
                        defaults={
                            "phone": f"9{random.randint(100000000,999999999)}",
                            "is_credit_enabled": True,
                            "credit_duration_days": 7,
                            "created_by": owner,
                        }
                    )
                    wc_objects.append(wc)

                # ── Historical Sales (50 per store) ───────────────
                existing_sales = Sale.objects.filter(store=store).count()
                to_create = max(0, 50 - existing_sales)
                for _ in range(to_create):
                    bill_type = random.choice(["RETAIL", "RETAIL", "RETAIL", "WHOLESALE"])
                    payment   = random.choice(["CASH", "UPI", "CASH", "CARD"])
                    num_items = random.randint(1, 4)
                    chosen    = random.sample(products, min(num_items, len(products)))

                    sale = Sale(
                        store=store,
                        bill_type=bill_type,
                        payment_mode=payment,
                        gst_rate=0,
                        discount=decimal.Decimal(random.choice([0, 0, 10, 20])),
                        customer_name="" if bill_type == "RETAIL" else random.choice(WS_CUSTOMERS),
                        created_by=staff,
                        created_at=timezone.now() - timezone.timedelta(days=random.randint(0, 60)),
                    )
                    sale.save()

                    subtotal = decimal.Decimal(0)
                    for p in chosen:
                        qty   = decimal.Decimal(str(round(random.uniform(0.5, 5.0), 3)))
                        price = p.retail_price if bill_type == "RETAIL" else p.wholesale_price
                        total = (qty * price).quantize(decimal.Decimal("0.01"))
                        subtotal += total
                        SaleItem.objects.create(
                            sale=sale, product=p,
                            product_name=p.name,
                            quantity=qty,
                            cost_price=p.cost_price,
                            selling_price=price,
                            total_amount=total,
                            total_cost=(qty * p.cost_price).quantize(decimal.Decimal("0.01")),
                            profit=total - (qty * p.cost_price).quantize(decimal.Decimal("0.01")),
                        )
                    sale.subtotal    = subtotal
                    sale.grand_total = max(decimal.Decimal(0), subtotal - sale.discount)
                    sale.save()

                # ── Ledger Book + Entries ──────────────────────────
                book, _ = LedgerBook.objects.get_or_create(
                    store=store, name="Cash Ledger",
                    defaults={"color": "#27a4d1", "icon": "fa-money-bill-wave", "created_by": owner}
                )
                existing_entries = LedgerEntry.objects.filter(ledger_book=book).count()
                for _ in range(max(0, 10 - existing_entries)):
                    LedgerEntry.objects.create(
                        store=store, ledger_book=book,
                        date=timezone.now().date() - timezone.timedelta(days=random.randint(0, 30)),
                        description=random.choice(["Fish purchase", "Salary payment", "Ice purchase", "Transport"]),
                        amount_given=decimal.Decimal(random.randint(0, 5000)),
                        amount_spent=decimal.Decimal(random.randint(0, 3000)),
                        created_by=owner,
                    )

                self.stdout.write(f"  ✓ [{i:02d}/30] {store_name}")

        # ── Write credentials file for locust ─────────────────────
        import json, os
        cred_path = os.path.join(os.path.dirname(__file__), "../../../../test_credentials.json")
        with open(cred_path, "w") as f:
            json.dump({"password": PASSWORD, "stores": credentials}, f, indent=2)

        self.stdout.write(f"\n✅ Done! {n_stores} stores seeded.")
        self.stdout.write(f"   Credentials saved to test_credentials.json")
        self.stdout.write(f"   Staff password: {PASSWORD}")
