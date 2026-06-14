import os
import django
import datetime
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'oceanwaves_project.settings')
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone
from pos.models import WholesaleCustomer, CreditRecord, CreditPayment

def run():
    admin_user = User.objects.filter(is_superuser=True).first()
    if not admin_user:
        admin_user = User.objects.first()

    print("🧹 Clearing all existing CreditPayments and resetting CreditRecord statuses...")
    CreditPayment.objects.all().delete()
    CreditRecord.objects.all().update(is_paid=False, paid_on=None)
    print("✅ Reset complete.")

    # 1. Define payments list from the user's spreadsheet + original Greenland VM data
    payments_data = [
        # JK Restaurant Payments
        {'customer': 'JK Restaurant', 'amount': 530.0, 'date': '2026-06-02', 'note': 'Settle credit of 01-06'},
        {'customer': 'JK Restaurant', 'amount': 820.0, 'date': '2026-06-02', 'note': 'Settle credit of 01-06'},
        {'customer': 'JK Restaurant', 'amount': 970.0, 'date': '2026-06-03', 'note': 'Settle credit of 02-06'},
        {'customer': 'JK Restaurant', 'amount': 380.0, 'date': '2026-06-03', 'note': 'Settle credit of 02-06'},
        {'customer': 'JK Restaurant', 'amount': 970.0, 'date': '2026-06-04', 'note': 'Settle credit of 03-06'},
        {'customer': 'JK Restaurant', 'amount': 380.0, 'date': '2026-06-04', 'note': 'Settle credit of 03-06'},
        {'customer': 'JK Restaurant', 'amount': 970.0, 'date': '2026-06-06', 'note': 'Partial payment for 05-06'},
        {'customer': 'JK Restaurant', 'amount': 1040.0, 'date': '2026-06-06', 'note': 'Partial payment for 05-06'},
        {'customer': 'JK Restaurant', 'amount': 970.0, 'date': '2026-06-07', 'note': 'Partial payment for 06-06'},
        {'customer': 'JK Restaurant', 'amount': 380.0, 'date': '2026-06-07', 'note': 'Partial payment for 06-06'},
        {'customer': 'JK Restaurant', 'amount': 1350.0, 'date': '2026-06-08', 'note': 'Settle credit of 07-06'},
        {'customer': 'JK Restaurant', 'amount': 1370.0, 'date': '2026-06-08', 'note': 'Partial payment for 08-06'},

        # Supreme Restaurant Payments
        {'customer': 'Supreme Restaurant', 'amount': 100.0, 'date': '2026-06-02', 'note': 'Partial payment for 02-06'},

        # Greenland Food Court Payments (Original VM data preserved)
        {'customer': 'Green Land Food Court', 'amount': 440.0, 'date': '2026-06-08', 'note': 'Paid via phonepe'},
    ]

    print("📝 Inserting CreditPayments...")
    for p in payments_data:
        customer = WholesaleCustomer.objects.get(name=p['customer'])
        pay_date = datetime.date.fromisoformat(p['date'])
        CreditPayment.objects.create(
            customer=customer,
            amount=Decimal(str(p['amount'])),
            payment_mode='CASH' if p['customer'] != 'Green Land Food Court' else 'UPI',
            note=p['note'],
            date=pay_date,
            created_by=admin_user
        )
        print(f"Recorded payment of ₹{p['amount']} for {customer.name} on {p['date']}")

    print("\n🧮 Allocating payments to CreditRecords using FIFO...")
    customers = WholesaleCustomer.objects.all()
    for c in customers:
        payments = c.payments.all().order_by('date', 'id')
        records = c.credit_records.all().order_by('due_date', 'id')
        
        if not payments.exists() or not records.exists():
            continue

        print(f"\nProcessing {c.name}:")
        
        # Build queue of payments
        payment_queue = []
        for p in payments:
            payment_queue.append({'amount': p.amount, 'date': p.date})

        payment_idx = 0
        for r in records:
            due = r.total_due
            # Allocate from payments queue
            allocated = Decimal('0.0')
            last_pay_date = None
            
            while allocated < due and payment_idx < len(payment_queue):
                p = payment_queue[payment_idx]
                needed = due - allocated
                available = p['amount']
                
                if available > 0:
                    transfer = min(needed, available)
                    allocated += transfer
                    p['amount'] -= transfer
                    last_pay_date = p['date']
                    
                if p['amount'] == 0:
                    payment_idx += 1
            
            if allocated >= due:
                r.is_paid = True
                r.paid_on = last_pay_date
                r.save()
                print(f"  ✅ Credit of ₹{due} (due {r.due_date}) marked PAID on {r.paid_on}")
            else:
                r.is_paid = False
                r.paid_on = None
                r.save()
                bal = due - allocated
                print(f"  ❌ Credit of ₹{due} (due {r.due_date}) marked UNPAID (Remaining Balance: ₹{bal})")

    print("\n🚀 Payments and credits synchronization completed successfully!")

if __name__ == '__main__':
    run()
