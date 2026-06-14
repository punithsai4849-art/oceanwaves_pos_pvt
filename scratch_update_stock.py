import os
import django
import datetime
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'oceanwaves_project.settings')
django.setup()

from pos.models import Product, Expense, StockLog, DailyStockSnapshot
from django.utils import timezone

def run():
    # ─────────────────────────────────────────────────────────────────────────
    # 1. Correct Stock for "white prawn 43" (ID 21)
    # ─────────────────────────────────────────────────────────────────────────
    print("🍤 Correcting white prawn 43 (ID 21)...")
    try:
        p21 = Product.objects.get(id=21)
        print(f"Current stock: {p21.stock_quantity}")
        
        # Update Product Stock
        p21.stock_quantity += Decimal('50.0')
        p21.save(update_fields=['stock_quantity'])
        print(f"New stock: {p21.stock_quantity}")
        
        # Update Expense ID 6
        exp6 = Expense.objects.filter(id=6).first()
        if exp6:
            exp6.product_id = 21
            exp6.quantity = Decimal('50.0')
            exp6.price_per_kg = Decimal('333.00')
            exp6.save(update_fields=['product_id', 'quantity', 'price_per_kg'])
            print("Updated Expense ID 6 details.")
            
        # Create StockLog
        # On June 8th, before purchase, closing stock was 17.0 - 3.0 sold = 14.0 closing.
        # With 50.0 purchase, the balance was 64.0
        dt_p21 = timezone.make_aware(datetime.datetime.combine(datetime.date(2026, 6, 8), datetime.time(10, 0)), timezone.get_current_timezone())
        log21 = StockLog.objects.create(
            store_id=1,
            product_id=21,
            movement='IN',
            quantity=Decimal('50.0'),
            balance=Decimal('64.0'),
            reference='Stock Purchase: 50 kg of white prawn 43',
            created_by_id=1
        )
        StockLog.objects.filter(id=log21.id).update(created_at=dt_p21)
        print("Created StockLog for June 8th.")

        # Update DailyStockSnapshot for white prawn 43
        snapshots21 = DailyStockSnapshot.objects.filter(product_id=21).order_by('date')
        for s in snapshots21:
            if s.date == datetime.date(2026, 6, 8):
                s.purchased_qty = Decimal('50.0')
                s.closing_qty += Decimal('50.0')
                s.save(update_fields=['purchased_qty', 'closing_qty'])
                print(f"Updated snapshot on {s.date}: closing={s.closing_qty}")
            elif s.date > datetime.date(2026, 6, 8):
                s.opening_qty += Decimal('50.0')
                s.closing_qty += Decimal('50.0')
                s.save(update_fields=['opening_qty', 'closing_qty'])
                print(f"Updated snapshot on {s.date}: opening={s.opening_qty}, closing={s.closing_qty}")
                
    except Product.DoesNotExist:
        print("Product 21 does not exist.")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Correct Stock for "Apollo fish" (ID 24)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n🐟 Correcting Apollo fish (ID 24)...")
    try:
        p24 = Product.objects.get(id=24)
        print(f"Current stock: {p24.stock_quantity}")
        
        # Update Product Stock
        p24.stock_quantity += Decimal('110.0')
        p24.save(update_fields=['stock_quantity'])
        print(f"New stock: {p24.stock_quantity}")
        
        # Update Expense ID 5
        exp5 = Expense.objects.filter(id=5).first()
        if exp5:
            exp5.product_id = 24
            exp5.quantity = Decimal('110.0')
            exp5.price_per_kg = Decimal('133.64')
            exp5.save(update_fields=['product_id', 'quantity', 'price_per_kg'])
            print("Updated Expense ID 5 details.")
            
        # Create StockLog
        # On June 8th closing was 25.0. On June 10th, 110.0 was purchased. New balance = 135.0
        dt_p24 = timezone.make_aware(datetime.datetime.combine(datetime.date(2026, 6, 10), datetime.time(10, 0)), timezone.get_current_timezone())
        log24 = StockLog.objects.create(
            store_id=1,
            product_id=24,
            movement='IN',
            quantity=Decimal('110.0'),
            balance=Decimal('135.0'),
            reference='Stock Purchase: 110 kg of Apollo fish',
            created_by_id=1
        )
        StockLog.objects.filter(id=log24.id).update(created_at=dt_p24)
        print("Created StockLog for June 10th.")

        # Update DailyStockSnapshot for Apollo fish
        snapshots24 = DailyStockSnapshot.objects.filter(product_id=24).order_by('date')
        for s in snapshots24:
            # Snapshots after June 10th
            if s.date >= datetime.date(2026, 6, 10):
                s.opening_qty += Decimal('110.0')
                s.closing_qty += Decimal('110.0')
                s.save(update_fields=['opening_qty', 'closing_qty'])
                print(f"Updated snapshot on {s.date}: opening={s.opening_qty}, closing={s.closing_qty}")
                
    except Product.DoesNotExist:
        print("Product 24 does not exist.")

if __name__ == '__main__':
    run()
