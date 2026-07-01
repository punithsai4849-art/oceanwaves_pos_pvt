import os
import django
import datetime
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'oceanwaves_project.settings')
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone
from pos.models import Store, Product, Sale, SaleItem, CreditRecord, CreditPayment, StockLog, DailyStockSnapshot, WholesaleCustomer

def run():
    print("🧹 Wiping transactional tables...")
    SaleItem.objects.all().delete()
    Sale.objects.all().delete()
    CreditRecord.objects.all().delete()
    CreditPayment.objects.all().delete()
    StockLog.objects.all().delete()
    DailyStockSnapshot.objects.all().delete()
    print("✅ Transactional data cleared!")

    # Fetch store and admin user
    store = Store.objects.get(id=1)
    admin_user = User.objects.filter(is_superuser=True).first()
    if not admin_user:
        admin_user = User.objects.first()

    # Initial stocks on 01-06-2026
    initial_stocks = {
        20: Decimal('0.5'),
        21: Decimal('71.0'),
        22: Decimal('23.0'),
        23: Decimal('0.5'),
        24: Decimal('53.0'),
        25: Decimal('12.0'),
        26: Decimal('3.25'),
        27: Decimal('3.7'),
        28: Decimal('1.0'),
        29: Decimal('1.0'),
        30: Decimal('6.0'),
        31: Decimal('0.0'),
        32: Decimal('0.0'),
    }

    # Reset active product stock quantities to initial
    print("⚙️ Resetting product stocks to June 1st opening values...")
    for pid, qty in initial_stocks.items():
        Product.objects.filter(id=pid).update(stock_quantity=qty)
    print("✅ Product stocks reset!")

    # Wholesale Customer mappings
    customer_map = {
        'JK Restaurant': WholesaleCustomer.objects.get(name='JK Restaurant'),
        'Harsha Food Ex': WholesaleCustomer.objects.get(name='Harsha Food Ex'),
        'Supreme Restaurant': WholesaleCustomer.objects.get(name='Supreme Restaurant'),
        'anandh restaurant': WholesaleCustomer.objects.get(name='anandh restaurant'),
        'Green Land Food Court': WholesaleCustomer.objects.get(name='Green Land Food Court'),
    }

    # Sales data list matching the user's itemized spreadsheet + Greenland original VM data
    sales_data = [
        # 01-06-2026
        {'date': '2026-06-01', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 21, 'qty': 3.0, 'price': 450.0},
        {'date': '2026-06-01', 'type': 'RETAIL', 'product_id': 21, 'qty': 0.5, 'price': 450.0},

        # 02-06-2026
        {'date': '2026-06-02', 'type': 'CREDIT', 'customer': 'Harsha Food Ex', 'product_id': 21, 'qty': 2.0, 'price': 450.0},
        {'date': '2026-06-02', 'type': 'CREDIT', 'customer': 'Supreme Restaurant', 'product_id': 21, 'qty': 2.0, 'price': 450.0},
        {'date': '2026-06-02', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 21, 'qty': 3.0, 'price': 450.0},

        # 03-06-2026
        {'date': '2026-06-03', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 21, 'qty': 3.0, 'price': 450.0},

        # 04-06-2026
        {'date': '2026-06-04', 'type': 'CREDIT', 'customer': 'Supreme Restaurant', 'product_id': 21, 'qty': 2.0, 'price': 450.0},

        # 05-06-2026
        {'date': '2026-06-05', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 21, 'qty': 3.0, 'price': 450.0},
        {'date': '2026-06-05', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 24, 'qty': 3.0, 'price': 220.0},
        {'date': '2026-06-05', 'type': 'CREDIT', 'customer': 'Supreme Restaurant', 'product_id': 21, 'qty': 2.0, 'price': 450.0},
        {'date': '2026-06-05', 'type': 'CREDIT', 'customer': 'Harsha Food Ex', 'product_id': 21, 'qty': 2.0, 'price': 450.0},
        {'date': '2026-06-05', 'type': 'CREDIT', 'customer': 'anandh restaurant', 'product_id': 21, 'qty': 10.0, 'price': 430.0},
        {'date': '2026-06-05', 'type': 'CREDIT', 'customer': 'anandh restaurant', 'product_id': 24, 'qty': 20.0, 'price': 200.0},
        {'date': '2026-06-05', 'type': 'RETAIL', 'product_id': 21, 'qty': 3.0, 'price': 450.0},

        # 06-06-2026
        {'date': '2026-06-06', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 21, 'qty': 3.0, 'price': 450.0},

        # 07-06-2026
        {'date': '2026-06-07', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 21, 'qty': 3.0, 'price': 450.0},
        {'date': '2026-06-07', 'type': 'CREDIT', 'customer': 'Harsha Food Ex', 'product_id': 21, 'qty': 2.0, 'price': 450.0},
        {'date': '2026-06-07', 'type': 'CREDIT', 'customer': 'anandh restaurant', 'product_id': 21, 'qty': 10.0, 'price': 430.0},
        {'date': '2026-06-07', 'type': 'RETAIL', 'product_id': 21, 'qty': 0.5, 'price': 450.0},
        {'date': '2026-06-07', 'type': 'RETAIL', 'product_id': 27, 'qty': 3.7, 'price': 350.0},
        {'date': '2026-06-07', 'type': 'RETAIL', 'product_id': 29, 'qty': 1.0, 'price': 730.0},

        # 08-06-2026 (Mapped from 08-05-2026 / 08-06-2026 dates in sheets)
        {'date': '2026-06-08', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 21, 'qty': 3.0, 'price': 450.0},
        {'date': '2026-06-08', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 24, 'qty': 3.0, 'price': 220.0},
        {'date': '2026-06-08', 'type': 'CREDIT', 'customer': 'Green Land Food Court', 'product_id': 24, 'qty': 2.0, 'price': 220.0},
        {'date': '2026-06-08', 'type': 'CREDIT', 'customer': 'Supreme Restaurant', 'product_id': 21, 'qty': 1.0, 'price': 450.0},

        # 11-06-2026 (Mapped from 11-05-2026 / 11-06-2026 in sheets)
        {'date': '2026-06-11', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 21, 'qty': 3.0, 'price': 450.0},
        {'date': '2026-06-11', 'type': 'CREDIT', 'customer': 'Harsha Food Ex', 'product_id': 21, 'qty': 2.0, 'price': 450.0},

        # 13-06-2026
        {'date': '2026-06-13', 'type': 'CREDIT', 'customer': 'JK Restaurant', 'product_id': 21, 'qty': 2.0, 'price': 450.0},
        {'date': '2026-06-13', 'type': 'CREDIT', 'customer': 'Green Land Food Court', 'product_id': 24, 'qty': 2.0, 'price': 220.0}, # Preserved
    ]

    current_stocks = dict(initial_stocks)

    # Date list from June 1st to June 13th
    date_list = [
        '2026-06-01',
        '2026-06-02',
        '2026-06-03',
        '2026-06-04',
        '2026-06-05',
        '2026-06-06',
        '2026-06-07',
        '2026-06-08',
        '2026-06-09',
        '2026-06-10',
        '2026-06-11',
        '2026-06-12',
        '2026-06-13',
    ]

    print("📝 Inserting sales and snapshots day by day...")
    for date_str in date_list:
        dt_date = datetime.date.fromisoformat(date_str)
        # Combine with custom time
        naive_dt = datetime.datetime.combine(dt_date, datetime.time(10, 0))
        aware_dt = timezone.make_aware(naive_dt, timezone.get_current_timezone())

        # Track daily opening stocks
        daily_openings = dict(current_stocks)

        # Process sales on this day
        sales_today = [s for s in sales_data if s['date'] == date_str]
        for s in sales_today:
            product = Product.objects.get(id=s['product_id'])
            qty = Decimal(str(s['qty']))
            price = Decimal(str(s['price']))
            cost_price = product.cost_price
            subtotal = qty * price

            if s['type'] == 'CREDIT':
                wc = customer_map[s['customer']]
                sale = Sale.objects.create(
                    store=store,
                    bill_type='WHOLESALE',
                    payment_mode='CREDIT',
                    wholesale_customer=wc,
                    customer_name=wc.name,
                    customer_phone=wc.phone,
                    customer_address=wc.address,
                    subtotal=subtotal,
                    grand_total=subtotal,
                    created_by=admin_user,
                )
                Sale.objects.filter(id=sale.id).update(created_at=aware_dt)
                
                # Create CreditRecord
                cr = CreditRecord.objects.create(
                    customer=wc,
                    sale=sale,
                    due_date=dt_date + datetime.timedelta(days=wc.credit_duration_days)
                )
                CreditRecord.objects.filter(id=cr.id).update(created_at=aware_dt)
            else:
                sale = Sale.objects.create(
                    store=store,
                    bill_type='RETAIL',
                    payment_mode='CASH',
                    customer_name='Walk-in',
                    subtotal=subtotal,
                    grand_total=subtotal,
                    created_by=admin_user,
                )
                Sale.objects.filter(id=sale.id).update(created_at=aware_dt)

            # Create SaleItem
            SaleItem.objects.create(
                sale=sale,
                product=product,
                product_name=product.name,
                quantity=qty,
                cost_price=cost_price,
                selling_price=price,
                total_amount=subtotal,
                total_cost=qty * cost_price,
                profit=subtotal - (qty * cost_price)
            )

            # Update stocks
            current_stocks[s['product_id']] -= qty

        # Create daily stock snapshots for all active products
        for pid in initial_stocks.keys():
            p = Product.objects.get(id=pid)
            opening = daily_openings[pid]
            sold = sum(Decimal(str(s['qty'])) for s in sales_today if s['product_id'] == pid)
            closing = current_stocks[pid]

            DailyStockSnapshot.objects.create(
                store=store,
                product=p,
                date=dt_date,
                opening_qty=opening,
                purchased_qty=Decimal('0.0'),
                sold_qty=sold,
                closing_qty=closing
            )

    # Update actual product stock levels in db
    print("💾 Updating current stock levels in the database...")
    for pid, qty in current_stocks.items():
        Product.objects.filter(id=pid).update(stock_quantity=qty)

    print("🚀 Completed database reset and reload successfully!")

if __name__ == '__main__':
    run()
