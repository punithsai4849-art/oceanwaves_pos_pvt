import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from pos.models import CreditRecord
from django.core.mail import send_mail

class Command(BaseCommand):
    help = 'Checks for wholesale credits due in 2 days and sends alerts.'

    def handle(self, *args, **options):
        target_date = timezone.now().date() + datetime.timedelta(days=2)
        records = CreditRecord.objects.filter(is_paid=False, due_date=target_date)
        
        count = 0
        for r in records:
            store_name = r.sale.store.name if (r.sale and r.sale.store) else 'Global'
            subject = f"Alert: Credit Payment Due Soon for {r.customer.name}"
            
            if r.is_external:
                msg = f"Dear {r.customer.name},\n\nYour credit payment of Rs {r.amount} for Reference '{r.external_reference}' is due on {r.due_date}.\nPlease clear the dues to maintain credit privileges.\n\nOCEANWAVES SEA FOODS is part of OCEANWAVES VICTUALS PRIVATE LIMITED.\n\nThank you,\n{store_name} (OCEANWAVES POS)"
            else:
                msg = f"Dear {r.customer.name},\n\nYour credit payment of Rs {r.sale.grand_total} for Bill #{r.sale.bill_number} is due on {r.due_date}.\nPlease clear the dues to maintain credit privileges.\n\nOCEANWAVES SEA FOODS is part of OCEANWAVES VICTUALS PRIVATE LIMITED.\n\nThank you,\n{store_name} (OCEANWAVES POS)"
            
            recipients = []
            if r.customer.email:
                recipients.append(r.customer.email)
            
            # Find store managers/incharges
            if r.sale and r.sale.store:
                incharges = list(r.sale.store.staff.filter(role__in=['OWNER', 'WHOLESALE_EXEC']).values_list('user__email', flat=True))
                # Add valid emails
                recipients += [e for e in incharges if e]

            # fallback logger
            ref = r.sale.bill_number if r.sale else r.external_reference
            print(f"[{timezone.now()}] Alert: {r.customer.name} (Ref #{ref}) due on {r.due_date}. Emails: {recipients}")
            
            if recipients:
                try:
                    send_mail(subject, msg, 'noreply@oceanwaves.com', recipients, fail_silently=True)
                    count += 1
                except Exception as e:
                    self.stderr.write(f"Failed to send email to {recipients}: {e}")

        self.stdout.write(self.style.SUCCESS(f'Checked credits. Sent {count} alerts.'))
